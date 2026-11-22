"""Runtime bridge from bound Lark Goal Topics into the existing Inbox path."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, overload

from ...chat_manager import MANAGER_AGENT_OBJECTIVE
from ...control_plane.collaboration import conversation_reply_context
from ...file_lock import exclusive_file_lock
from .manager_routing import (
    has_manager_binding,
    invalid_manager_authority_result,
    manager_session_requires_executor_rebind,
    manager_turn_executor,
    parse_manager_authority_mode,
    unavailable_manager_context_result,
    ManagerAuthorityMode,
)
from ..external_connector_runtime import (
    ExternalEffectKind,
    ExternalResponsePolicy,
    build_external_event_response_receipt,
    decide_external_event_ack,
)
from .event_collector_runtime import EVENT_DIAGNOSTIC_PREFIX, EVENT_READY_PREFIX
from .event_inbox import (
    LarkTopicInput,
    acknowledge_lark_event_inbox,
    ingest_lark_event_inbox,
    inspect_lark_event_inbox,
)
from .identity_shapes import LARK_MESSAGE_ID_PATTERN as MESSAGE_ID_PATTERN
from .goal_channel_contracts import (
    LarkTopicEventDecisionReason,
    bindings_for_goal,
    normalize_lark_topic_event_rejection_reason,
)
from .goal_channel_targets import goal_channel_target_for_name
from .goal_channel_transport import lark_provider_mention_identities, record_event_timing
from .goal_topic_connections import decide_lark_topic_event
from .goal_topic_dispatch import (
    MentionCapture,
    ProfileEventDispatch,
    ProfileTopicIntake,
    TopicIntakeRoute,
    profile_can_reserve_mention_worker,
    topic_inbox_key,
    topic_intake_route,
)
from .goal_topic_routing import IngressMode
from .inbox_reply import CommandRunner, reply_lark_event_inbox
from .manager_reply_delivery import (
    load_delivery as _load_manager_delivery,
    pending_delivery as _pending_manager_delivery,
    text_digest as _manager_delivery_text_digest,
    write_delivery as _write_manager_delivery,
)
from .manager_context import (
    MANAGER_CONTEXT_ITEM_LIMIT,
    manager_context_materials,
    manager_context_projection,
    manager_failure_reply as _manager_failure_reply,
    manager_message,
    opaque_digest as _opaque_digest,
    restore_manager_context_route,
    session_turn_effect as _session_turn_effect,
    settle_manager_context,
    sync_manager_context,
)
from .manager_reply_parts import (
    manager_part_delivery_readback,
)
from .manager_reply_format import repair_manager_reply_text
from .private_json import write_private_json_atomic
from .outbound import LarkOutboundTextError, present_lark_conversation_text
from .inbox_reactions import (
    _create_reaction,
    _delete_reaction,
    ensure_lark_event_inbox_received_reaction,
)
from .team_plan_confirmation import (
    TeamPlanReviewCallbackStream,
    proposal_ids_after_turn,
    settle_team_plan_proposal_delivery,
    start_team_plan_review_callback_stream,
)

if TYPE_CHECKING:
    from ...chat_runtime import ChatRuntimeController


class _StreamProcess(Protocol):
    @property
    def stdout(self) -> Iterable[str] | None: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


Answer = Callable[[Mapping[str, object], str], str | Mapping[str, object]]
SnapshotProvider = Callable[[], Mapping[str, object]]
SimpleRunner = Callable[[Sequence[str]], Mapping[str, object]]
ProcessFactory = Callable[[list[str]], _StreamProcess]
HealthSink = Callable[[Mapping[str, object]], None]
ProposalDeliverer = Callable[[Mapping[str, object], list[str]], Mapping[str, object]]
ReviewCallbackHandler = Callable[[Mapping[str, object]], Mapping[str, object]]
ProviderRunner = Callable[..., object]
EventLane = tuple[str, ...]
CapturedEventLane = tuple[EventLane, dict[str, object] | None, TopicIntakeRoute | None]


class LarkGoalTopicTurnFailed(RuntimeError):
    """A terminal runtime receipt, without copying arbitrary upstream details."""

    def __init__(self, error_code: str, effect_receipt: Mapping[str, object]) -> None:
        super().__init__("Lark Goal Topic turn did not complete")
        self.error_code = error_code
        self.effect_receipt = effect_receipt


_EVENT_PROJECTION = (
    '{schema_version:"lark_event_inbox_event_v0",'
    "event_id:(.event_id // .header.event_id // .message_id // .event.message.message_id // .id),"
    "message_id:(.message_id // .message.message_id // .event.message.message_id // .id),"
    "create_time:(.create_time // .message.create_time // .event.message.create_time),"
    "content:(.content // .message.content // .event.message.content),"
    "sender_id:(.sender_id.open_id? // .sender_id // .sender.id // .sender.sender_id.open_id? // .sender.sender_id "
    "// .event.sender.sender_id.open_id? // .event.sender.sender_id // .event.sender.id),"
    "sender_type:(.sender_type // .sender.sender_type // .event.sender.sender_type),"
    "chat_id:(.chat_id // .message.chat_id // .event.message.chat_id),chat_type:(.chat_type // .message.chat_type // .event.message.chat_type),"
    "message_type:(.message_type // .msg_type // .message.message_type // .event.message.message_type),"
    "root_id:(.root_id // .message.root_id // .event.message.root_id),"
    "parent_id:(.parent_id // .reply_to // .message.parent_id // .message.reply_to "
    "// .event.message.parent_id // .event.message.reply_to),"
    "thread_id:(.thread_id // .message.thread_id // .event.message.thread_id),"
    "mentions:(.mentions // .message.mentions // .event.message.mentions // [])}"
)

_EVENT_EXIT_REASON = re.compile(r"\(reason: (limit|timeout|signal)\)$")


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_dict(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _str_field(mapping: Mapping[str, object], key: str, default: str = "") -> str:
    return str(mapping.get(key) or default)


def _int_field(mapping: Mapping[str, object], key: str, default: int = 0) -> int:
    value = mapping.get(key)
    if isinstance(value, bool):
        return int(value) if value else default
    return int(value) if isinstance(value, (int, float, str)) and value != "" else default


def _str_list(value: object, *, skip_empty: bool = False) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items = [str(item) for item in value]
    return [item for item in items if item] if skip_empty else items


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_as_dict(item) for item in value if isinstance(item, Mapping)]


def _iter_enabled_bindings(
    binding_payloads: Mapping[str, object],
) -> Iterator[tuple[str, Mapping[str, object], Mapping[str, object]]]:
    for goal_id, payload in binding_payloads.items():
        if not isinstance(payload, Mapping):
            continue
        for binding in bindings_for_goal(payload, str(goal_id)):
            if binding.get("enabled") is True:
                yield str(goal_id), payload, binding


def _event_consume_args(
    cli_bin: str, profile: str, *, timeout: str, max_events: str, quiet: bool = False,
) -> list[str]:
    args = [
        cli_bin, "--profile", profile, "event", "consume", "im.message.receive_v1",
        "--as", "bot", "--timeout", timeout, "--max-events", max_events, "--jq", _EVENT_PROJECTION,
    ]
    return [*args, "--quiet"] if quiet else args


def _empty_poll_result(status: str, *, ok: bool = True) -> dict[str, object]:
    return {"ok": ok, "status": status, "event_count": 0, "replied_count": 0}


def _topic_error(
    status: str, goal_id: object, *, inbox_config_ref: str | None = None,
    source_acknowledged: bool | None = None, **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"ok": False, "status": status, "goal_id": goal_id, **extra}
    if inbox_config_ref is not None:
        payload["inbox_config_ref"] = inbox_config_ref
    if source_acknowledged is not None:
        payload["source_acknowledged"] = source_acknowledged
    return payload


def _persist_delivery(path: Path, state: dict[str, object], **updates: object) -> None:
    if updates:
        state.update(updates)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_manager_delivery(path, state)


def _active_group_profile_configs(snapshot: Mapping[str, object]) -> dict[str, dict[str, str]]:
    target_payload = _as_mapping(snapshot.get("target_payload"))
    binding_payloads = _as_mapping(snapshot.get("binding_payloads"))
    profiles: dict[str, dict[str, str]] = {}
    for _goal_id, _payload, binding in _iter_enabled_bindings(binding_payloads):
        target = goal_channel_target_for_name(target_payload, _str_field(binding, "target_ref"))
        if target is None or target.get("enabled") is not True:
            continue
        identity = _as_mapping(target.get("identity"))
        profile = _str_field(identity, "sender_profile").strip()
        if profile:
            profiles.setdefault(
                profile,
                {
                    "cli_bin": _str_field(identity, "cli_bin", "lark-cli"),
                    "bot_app_id": _str_field(identity, "bot_app_id"),
                },
            )
    return profiles


def _active_profile_configs(snapshot: Mapping[str, object]) -> dict[str, dict[str, str]]:
    from .conversation_identity import identity_ref

    profiles = _active_group_profile_configs(snapshot)
    for profile, raw_config in _as_mapping(snapshot.get("private_profiles")).items():
        config = dict(raw_config) if isinstance(raw_config, Mapping) else {}
        if profile in profiles:
            stored_app_id = profiles[profile].get("bot_app_id")
            app_id = stored_app_id or config.get("bot_app_id")
            if (config.get("audience", "owner") != "owner" or not app_id
                    or identity_ref(str(app_id)) != config.get("provider_ref")):
                raise ValueError("shared listener requires the same verified App and separate audiences")
            profiles[str(profile)] = {**profiles[profile], **config}
            if not stored_app_id:
                # Keep the pre-upgrade group lease until its original owner
                # exits. Verified private discovery supplies identity, not a
                # reason to start a second consumer under a new App key.
                profiles[str(profile)]["consumer_ref"] = hashlib.sha256(str(profile).encode("utf-8")).hexdigest()[:32]
        else:
            profiles[str(profile)] = config
    return profiles


def _native_profile_event(snapshot: Mapping[str, object], profile: str, event: Mapping[str, object]) -> bool:
    """Select a transport lane only; its original owner still admits the input.

    An owner-private binding may share one consumer with legacy group routes.
    A private rejection never falls through to a group authority or vice versa.
    """
    config = _as_mapping(snapshot.get("private_profiles")).get(profile)
    return isinstance(config, Mapping) and not (
        config.get("audience", "owner") == "owner" and event.get("chat_type") == "group"
        and profile in _active_group_profile_configs(snapshot)
    )


def _default_simple_runner(args: Sequence[str]) -> Mapping[str, object]:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {"returncode": 1, "stdout": "", "stderr": ""}
    return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def _default_process_factory(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )


def _target_for_profile_chat(
    target_payload: Mapping[str, object],
    *,
    profile: str,
    chat_id: str,
    bot_app_id: str = "",
    active_target_refs: set[str] | None = None,
    root_id: str = "",
    binding_payloads: Mapping[str, object] | None = None,
) -> tuple[str, Mapping[str, object]] | None:
    targets = _as_mapping(target_payload.get("targets"))
    candidates: list[tuple[str, Mapping[str, object]]] = []
    for target_ref, target in targets.items():
        if active_target_refs is not None and str(target_ref) not in active_target_refs:
            continue
        if not isinstance(target, Mapping) or target.get("enabled") is not True:
            continue
        channel = _as_mapping(target.get("channel"))
        identity = _as_mapping(target.get("identity"))
        same_app = bool(bot_app_id) and _str_field(identity, "bot_app_id") == bot_app_id
        if _str_field(channel, "chat_id") == chat_id and (
            same_app or _str_field(identity, "sender_profile") == profile
        ):
            candidates.append((str(target_ref), target))
    if len(candidates) == 1:
        return candidates[0]
    bindings = _as_mapping(binding_payloads)
    if root_id:
        rooted = [c for c in candidates if root_id in _topic_roots_for_target(bindings, target_ref=c[0])]
        if len(rooted) == 1:
            return rooted[0]
        if rooted:
            return None
    managers = [c for c in candidates if has_manager_binding(bindings, c[0])]
    return managers[0] if len(managers) == 1 else None


def _topic_roots_for_target(binding_payloads: Mapping[str, object], *, target_ref: str) -> list[str]:
    roots: list[str] = []
    for goal_id, payload in binding_payloads.items():
        if isinstance(payload, Mapping):
            roots.extend(_topic_roots_for_bindings(bindings_for_goal(payload, str(goal_id)), target_ref=target_ref))
    return roots


def _topic_roots_for_bindings(
    bindings: Sequence[Mapping[str, object]], *, target_ref: str
) -> list[str]:
    roots: list[str] = []
    for binding in bindings:
        if binding.get("enabled") is not True or _str_field(binding, "target_ref") != target_ref:
            continue
        root_id = str(
            _as_mapping(binding.get("topic")).get("root_message_id")
            or _as_mapping(binding.get("channel")).get("pinned_message_id")
            or ""
        )
        if MESSAGE_ID_PATTERN.fullmatch(root_id):
            roots.append(root_id)
    return roots


def _binding_payloads_for_target(
    binding_payloads: Mapping[str, object], *, target_ref: str
) -> dict[str, Mapping[str, object]]:
    return {
        goal_id: payload
        for goal_id, payload, binding in _iter_enabled_bindings(binding_payloads)
        if _str_field(binding, "target_ref") == target_ref
    }


def _event_payloads(stdout: object) -> list[Mapping[str, object]]:
    events: list[Mapping[str, object]] = []
    for line in str(stdout or "").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            events.append(payload)
        elif isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, Mapping))
    return events


def _profile_event_route(
    *,
    profile: str,
    snapshot: Mapping[str, object],
    event: Mapping[str, object],
) -> tuple[
    tuple[Mapping[str, object], dict[str, Mapping[str, object]], dict[str, object]] | None, str
]:
    """Select the existing profile/target/Topic scope; never grant turn authority."""

    profile_config = _active_profile_configs(snapshot).get(profile)
    if profile_config is None:
        return None, "inactive"
    target_payload = _as_mapping(snapshot.get("target_payload"))
    binding_payloads = _as_mapping(snapshot.get("binding_payloads"))
    active_target_refs = {
        _str_field(binding, "target_ref") for _, _, binding in _iter_enabled_bindings(binding_payloads)
    }
    target_match = _target_for_profile_chat(
        target_payload, profile=profile, chat_id=_str_field(event, "chat_id"),
        bot_app_id=str(profile_config.get("bot_app_id") or ""),
        active_target_refs=active_target_refs, root_id=_str_field(event, "root_id"),
        binding_payloads=binding_payloads,
    )
    if target_match is None:
        return None, "target_unmatched"
    target_ref, _target = target_match
    routed_event = dict(event)
    root_id = _str_field(routed_event, "root_id")
    if not MESSAGE_ID_PATTERN.fullmatch(root_id) and not has_manager_binding(binding_payloads, target_ref):
        candidate_roots = _topic_roots_for_target(binding_payloads, target_ref=target_ref)
        if len(candidate_roots) > 1:
            return None, "topic_context_ambiguous"
        if not candidate_roots:
            return None, "topic_context_missing"
        routed_event["root_id"] = candidate_roots[0]
    return (target_payload, _binding_payloads_for_target(binding_payloads, target_ref=target_ref), routed_event), "matched"


@overload
def _profile_event_lane(
    *, profile: str, snapshot: Mapping[str, object], event: Mapping[str, object],
    runtime_root: str | Path, capture_mention: MentionCapture | None = None,
    include_capture: Literal[False] = False, prepare_intake: bool = False,
) -> EventLane: ...


@overload
def _profile_event_lane(
    *, profile: str, snapshot: Mapping[str, object], event: Mapping[str, object],
    runtime_root: str | Path, capture_mention: MentionCapture | None = None,
    include_capture: Literal[True], prepare_intake: bool = False,
) -> CapturedEventLane: ...


def _profile_event_lane(
    *,
    profile: str,
    snapshot: Mapping[str, object],
    event: Mapping[str, object],
    runtime_root: str | Path,
    capture_mention: MentionCapture | None = None,
    include_capture: bool = False,
    prepare_intake: bool = False,
) -> EventLane | CapturedEventLane:
    """Capture a live target before joining the ordinary reply lane."""

    def result(
        lane: EventLane, capture: dict[str, object] | None = None,
        intake: TopicIntakeRoute | None = None,
    ) -> EventLane | CapturedEventLane:
        return (lane, capture, intake) if include_capture else lane

    private_config = _as_mapping(snapshot.get("private_profiles")).get(profile)
    if isinstance(private_config, Mapping) and _native_profile_event(snapshot, profile, event):
        from .conversation_identity import lark_private_source

        try:
            raw_provider_ref = private_config["provider_ref"]
            if not isinstance(raw_provider_ref, str):
                raise TypeError("provider_ref must be a string")
            private_source = lark_private_source(provider_ref=raw_provider_ref, event=event)
        except (KeyError, TypeError, ValueError):
            pass  # Malformed hints retain the conservative unmatched lane.
        else:
            # Native profiles bypass legacy Goal routing at admission too.
            # Use the same App/chat/topic (or private sender) source shape for
            # scheduling, never a grant or a persisted Session selection. Core
            # still verifies the provider and acquires its fresh source fence.
            return result(("private." + str(private_source["source_ref"]),))

    scoped, _status = _profile_event_route(profile=profile, snapshot=snapshot, event=event)
    if scoped is not None:
        targets, bindings, routed_event = scoped
        decision = decide_lark_topic_event(
            target_payload=targets, binding_payloads=bindings,
            event=routed_event, runtime_root=runtime_root,
        )
        route = _as_mapping(decision.get("route"))
        if route:
            if route.get("conversation_kind") == "manager":
                source = "manager." + _opaque_digest(route.get("app_ref"), event.get("chat_id"))
            else:
                # Compatibility direct_session opens/resumes this exact channel.
                source = "topic." + _opaque_digest(
                    route.get("app_ref"), route.get("target_ref"),
                    route.get("topic_root_message_id"),
                )
            capture = (
                capture_mention(route) if capture_mention is not None
                and _route_native_mention(routed_event, route, targets) else None
            )
            if capture and capture.get("target_turn_id"):
                return result(("steer." + source, "steer.session." + str(route["session_id"])), capture)
            # A compatibility Topic may share this reader without a Session.
            # Persist its ordinary inputs before waiting for its reply lane.
            intake = topic_intake_route(profile, route, targets, routed_event) if prepare_intake else None
            if route.get("session_id"):
                return result((source, "session." + str(route["session_id"])), capture, intake)
            return result((source,), capture, intake)
    return result(("unmatched." + _opaque_digest(profile, event.get("chat_id")),))


def poll_lark_goal_topic_profile_once(
    *,
    profile: str,
    snapshot: Mapping[str, object],
    runtime_root: str | Path,
    answer: Answer,
    consume_runner: SimpleRunner = _default_simple_runner,
    provider_runner: ProviderRunner = subprocess.run,
    reply_runner: CommandRunner = _default_simple_runner,
    proposal_deliverer: ProposalDeliverer | None = None,
    private_admitter: Callable[[str, dict[str, object]], Mapping[str, object]] | None = None,
    mention_capture: Mapping[str, object] | None = None,
    live_turn_capture: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Consume one bounded event batch for an App and reuse Inbox reply/ACK."""

    profile_config = _active_profile_configs(snapshot).get(profile)
    if profile_config is None:
        return _empty_poll_result("inactive")
    result = consume_runner(
        _event_consume_args(str(profile_config["cli_bin"]), profile, timeout="5s", max_events="50", quiet=True)
    )
    if _int_field(result, "returncode") != 0:
        return _empty_poll_result("consume_failed", ok=False)

    events = _event_payloads(result.get("stdout"))
    raw_contexts = snapshot.get("goal_contexts")
    goal_contexts = (
        {str(k): v for k, v in raw_contexts.items() if isinstance(v, Mapping)}
        if isinstance(raw_contexts, Mapping) else {}
    )
    replied_count = 0
    event_statuses: list[str] = []
    event_reasons: list[str | None] = []
    for event in events:
        if _native_profile_event(snapshot, profile, event):
            private_result = (
                private_admitter(profile, dict(event))
                if private_admitter
                else {"status": "private_admission_unavailable"}
            )
            event_statuses.append(_str_field(private_result, "status", "unknown"))
            event_reasons.append(None)
            continue
        scoped, rejection = _profile_event_route(profile=profile, snapshot=snapshot, event=event)
        if scoped is None:
            event_statuses.append(rejection)
            event_reasons.append(None)
            continue
        target_payload, binding_payloads, routed_event = scoped
        try:
            event_result = process_lark_goal_topic_event(
                target_payload=target_payload, binding_payloads=binding_payloads, event=routed_event,
                runtime_root=runtime_root, goal_contexts=goal_contexts, answer=answer,
                reply_runner=reply_runner, provider_runner=provider_runner,
                proposal_deliverer=proposal_deliverer, mention_capture=mention_capture,
                live_turn_capture=live_turn_capture,
            )
        except Exception:
            event_statuses.append("processing_failed")
            event_reasons.append(None)
            continue
        event_statuses.append(_str_field(event_result, "status", "unknown"))
        event_reasons.append(_str_field(event_result, "reason") or None)
        replied_count += int(event_result.get("status") == "replied_and_acknowledged")
    return {
        "ok": True, "status": "polled", "event_count": len(events), "replied_count": replied_count,
        "event_statuses": event_statuses, "event_reasons": event_reasons,
    }


def stream_lark_goal_topic_profile(
    *,
    profile: str,
    snapshot_provider: SnapshotProvider,
    stop: threading.Event,
    runtime_root: str | Path,
    answer: Answer,
    process_factory: ProcessFactory = _default_process_factory,
    provider_runner: ProviderRunner = subprocess.run,
    reply_runner: CommandRunner = _default_simple_runner,
    health_sink: HealthSink | None = None,
    proposal_deliverer: ProposalDeliverer | None = None,
    private_admitter: Callable[[str, dict[str, object]], Mapping[str, object]] | None = None,
    capture_mention: MentionCapture | None = None,
    review_callback_handler: ReviewCallbackHandler | None = None,
    callback_availability_sink: Callable[[TeamPlanReviewCallbackStream], None] | None = None,
) -> dict[str, object]:
    """Keep one bounded long-lived CLI consumer attached between messages."""

    snapshot = snapshot_provider()
    profile_config = _active_profile_configs(snapshot).get(profile)
    if profile_config is None:
        return _empty_poll_result("inactive")
    cli_bin = str(profile_config["cli_bin"])
    process = process_factory(_event_consume_args(cli_bin, profile, timeout="30m", max_events="0"))
    callback_disconnected = threading.Event()
    callback_stream = (
        start_team_plan_review_callback_stream(
            snapshot=snapshot, profile=profile, cli_bin=cli_bin, process_factory=process_factory,
            parent_stop=stop, handler=review_callback_handler, availability_sink=callback_availability_sink,
        )
        if review_callback_handler is not None else None
    )
    if health_sink is not None:
                #   Keep the connection non-ready until the
        # provider emits its explicit ready marker (or a real event arrives).
        health_sink({"status": "starting", "error_code": None})
    watcher_done = threading.Event()
    configuration_removed = threading.Event()

    def terminate_streams(*, include_callback: bool = True) -> None:
        if process.poll() is None:
            process.terminate()
        if include_callback and callback_stream is not None:
            callback_stream.terminate()

    def stop_consumer() -> None:
        while not watcher_done.wait(1.0):
            if stop.is_set():
                terminate_streams()
                return
            if dispatch.failed:
                # A worker can fail while stdout waits indefinitely for the
                # next event. Unblock that reader so close drains active work
                # and raises into the existing service retry owner. Do not set
                # stop: failure must not become a requested, non-retrying exit.
                try:
                    with result_lock:
                        if health_sink is not None:
                            health_sink({"status": "failed", "error_code": "lark_event_listener_failed"})
                finally:
                    terminate_streams()
                return
            if callback_stream is not None and callback_stream.disconnected():
                callback_disconnected.set()
                terminate_streams(include_callback=False)
                return
            try:
                configured = profile in _active_profile_configs(snapshot_provider())
            except Exception:
                # A transient source read must not tear down a healthy route.
                continue
            if not configured:
                configuration_removed.set()
                stop.set()
                terminate_streams()
                return

    watcher = threading.Thread(target=stop_consumer, name=f"loopx-lark-stop-{profile}", daemon=True)
    event_count = 0
    replied_count = 0
    provider_ready = False
    exit_reason: str | None = None
    result_lock = threading.Lock()

    def note_provider_ready() -> None:
        nonlocal provider_ready
        with result_lock:
            if provider_ready:
                return
            provider_ready = True
            if health_sink is not None and not dispatch.failed:
                health_sink({"status": "listening", "error_code": None})

    def report_result(result: Mapping[str, object]) -> None:
        nonlocal event_count, replied_count
        events = _int_field(result, "event_count")
        if not events:
            return
        replies = _int_field(result, "replied_count")
        statuses = _str_list(result.get("event_statuses"))
        raw_reasons = result.get("event_reasons")
        reasons = (
            list(raw_reasons)
            if isinstance(raw_reasons, Sequence) and not isinstance(raw_reasons, (str, bytes))
            else []
        )
        with result_lock:
            event_count += events
            replied_count += replies
            if health_sink is not None:
                failed = dispatch.failed
                health_sink({
                    "status": "failed" if failed else "listening" if provider_ready else "starting",
                    "error_code": "lark_event_listener_failed" if failed else None,
                    "event_count": events, "replied_count": replies,
                    "last_event_status": statuses[-1] if statuses else None,
                    "last_event_reason": str(reasons[-1]) if reasons and reasons[-1] else None,
                })
            print(json.dumps({
                "schema_version": "loopx_lark_goal_topic_runtime_event_v0",
                "event_count": events, "replied_count": replies,
                "event_statuses": statuses, "event_reasons": reasons,
            }, separators=(",", ":")), flush=True)

    def dispatch_event(
        event: Mapping[str, object], mention: Mapping[str, object] | None,
        current_snapshot: Mapping[str, object], *, live_turn_capture: Mapping[str, object] | None = None,
    ) -> None:
        message_id = _str_field(event, "message_id")
        record_event_timing(profile, message_id, "dispatch_started")
        record_event_timing(profile, message_id, "dispatch_snapshot_ready")
        result = poll_lark_goal_topic_profile_once(
            profile=profile, snapshot=current_snapshot, runtime_root=runtime_root, answer=answer,
            consume_runner=lambda _args: {"returncode": 0, "stdout": json.dumps(event, ensure_ascii=False), "stderr": ""},
            provider_runner=provider_runner, reply_runner=reply_runner,
            proposal_deliverer=proposal_deliverer, private_admitter=private_admitter,
            mention_capture=mention, live_turn_capture=live_turn_capture,
        )
        record_event_timing(profile, message_id, "dispatch_returned")
        report_result(result)

    def can_steer(state: Mapping[str, object]) -> bool:
        if capture_mention is None:
            return False
        config = _active_profile_configs(state).get(profile)
        return config is not None and profile_can_reserve_mention_worker(
            state, profile, bot_app_id=str(config.get("bot_app_id") or ""), capture_mention=capture_mention,
        )

    reserve_priority = can_steer(snapshot)
    root = Path(runtime_root).expanduser().resolve()
    intake = ProfileTopicIntake(root, _opaque_digest(profile_config.get("bot_app_id") or profile))

    def report_retained_input() -> None:
        report_result({"event_count": 1, "replied_count": 0, "event_statuses": ["already_captured"], "event_reasons": [None]})

    def matches_input(inbox_key: str, packet: LarkTopicInput, current: Mapping[str, object]) -> bool:
        lane, _, route = _profile_event_lane(
            profile=profile, snapshot=current, event=packet["event"],
            runtime_root=root, include_capture=True, prepare_intake=True,
        )
        if route is None:
            return False
        ingress_mode = route.route.get("ingress_mode", IngressMode.DIRECT_SESSION.value)
        # Previously captured compatibility input still belongs to its Topic
        # even if another connection no longer reserves a steering worker.
        return (
            (can_steer(current) or ingress_mode == IngressMode.DIRECT_SESSION.value)
            and route.inbox_key == inbox_key
            and route.digest == packet["route_ref"] and lane == packet["lane"]
            and (
                ingress_mode != IngressMode.LIVE_STEERING.value
                or packet.get("live_turn_capture") is not None
                or packet["mention_capture"] is not None
            )
        )

    def prepare_input(inbox_key: str, packet: LarkTopicInput) -> Callable[[], None]:
        fresh = snapshot_provider()
        if matches_input(inbox_key, packet, fresh):
            return lambda: dispatch_event(
                packet["event"], packet["mention_capture"], fresh,
                live_turn_capture=packet.get("live_turn_capture"),
            )
        return lambda: report_result({
            "event_count": 1, "replied_count": 0, "event_statuses": ["ignored"],
            "event_reasons": [LarkTopicEventDecisionReason.BINDING_UNAVAILABLE.value],
        })

    def handle_event(line: str) -> None:
        envelope = json.loads(line)
        if "intake" in envelope:
            intake.dispatch_next(dispatch, envelope["intake"], tuple(envelope["lane"]), stop, prepare_input)
        else:
            dispatch_event(envelope["event"], envelope["mention_capture"], snapshot_provider())

    dispatch = ProfileEventDispatch(handle_event, stop, reserve_priority=reserve_priority)
    watcher.start()
    stream_failed = True
    try:
        intake.restore(dispatch, lambda key, packet: matches_input(key, packet, snapshot))
        stdout = process.stdout
        if stdout is None:
            return _empty_poll_result("stream_failed", ok=False)
        for line in stdout:
            if stop.is_set():
                break
            stripped = line.strip()
            if stripped.startswith(EVENT_READY_PREFIX):
                note_provider_ready()
                continue
            if stripped.startswith("[event] exited "):
                match = _EVENT_EXIT_REASON.search(stripped)
                exit_reason = match.group(1) if match else None
                continue
            if stripped.startswith(EVENT_DIAGNOSTIC_PREFIX):
                continue
            # Batch-shaped provider records are split before scheduling, so an
            # unrelated conversation in a batch can progress independently too.
            for event in _event_payloads(line):
                # Recovered local input alone does not attest provider readiness.
                note_provider_ready()
                message_id = _str_field(event, "message_id")
                record_event_timing(profile, message_id, "received")
                if intake.retains(message_id):
                    # A provider retry is the same source input even if the
                    # App or Session is now routed to a different Goal Topic.
                    report_retained_input()
                    continue
                # The App can save a compatible binding after this consumer
                # starts. Adopt its current eligibility on the same stream;
                # unqualified profiles retain all ordinary worker capacity.
                current_snapshot = snapshot_provider()
                reserve_priority = can_steer(current_snapshot)
                dispatch.set_priority_reservation(reserve_priority)
                lane, mention_capture, intake_route = _profile_event_lane(
                    profile=profile, snapshot=current_snapshot, event=event, runtime_root=runtime_root,
                    capture_mention=capture_mention if reserve_priority else None,
                    include_capture=True, prepare_intake=reserve_priority,
                )
                record_event_timing(profile, message_id, "lane_ready")
                priority = bool(_as_mapping(mention_capture).get("target_turn_id"))
                if reserve_priority and intake_route is not None and not priority:
                    live_turn_capture = None
                    if (intake_route.route.get("ingress_mode") == "live_steering"
                            and mention_capture is None and capture_mention is not None):
                        # A delayed ordinary live input keeps its original Turn.
                        live_turn_capture = capture_mention(intake_route.route)
                    if intake.capture(
                        dispatch, intake_route, lane, event, mention_capture,
                        lambda: _inbox_config(
                            runtime_root=root, route=intake_route.route, target_payload=intake_route.targets,
                        )[0],
                        live_turn_capture=live_turn_capture,
                    ):
                        record_event_timing(profile, message_id, "inbox_captured")
                    else:
                        report_retained_input()
                else:
                    envelope = json.dumps({"event": event, "mention_capture": mention_capture}, ensure_ascii=False)
                    if not dispatch.submit(lane, envelope, priority=priority):
                        break
        stream_failed = False
    finally:
        try:
            dispatch.close(cancel_pending=stream_failed)
        finally:
            watcher_done.set()
            if process.poll() is None:
                process.terminate()
            try:
                returncode = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait(timeout=3)
            watcher.join(timeout=1)
            if callback_stream is not None:
                callback_stream.close()
    stopped = stop.is_set()
    # A bus can die after registering the consumer and tell the CLI to exit
    # successfully with reason=signal (e.g. a Feishu/Lark domain mismatch).
    # Only our own stop or the requested bound is a planned stream ending.
    unexpected_exit = (
        provider_ready and not stopped
        and (callback_disconnected.is_set() or returncode != 0 or exit_reason not in {"limit", "timeout"})
    )
    return {
        "ok": configuration_removed.is_set() or stopped or (returncode == 0 and provider_ready and not unexpected_exit),
        **(
            {"error_code": "lark_review_callback_source_disconnected" if callback_disconnected.is_set() else "lark_event_source_disconnected"}
            if unexpected_exit else {}
        ),
        "status": (
            "configuration_removed" if configuration_removed.is_set()
            else "stopped" if stopped
            else "source_disconnected" if unexpected_exit
            else "stream_ended" if provider_ready
            else "stream_not_ready"
        ),
        "event_count": event_count,
        "replied_count": replied_count,
    }


def _bound_topic_session(
    route: Mapping[str, object], runtime_controller: ChatRuntimeController
) -> Mapping[str, object] | None:
    session_id = str(route.get("session_id") or "")
    store = getattr(runtime_controller, "store", None)
    if not session_id or store is None:
        return None
    session = store.load_session(session_id)
    manager = route.get("conversation_kind") == "manager"
    agent_id = manager_turn_executor(runtime_controller) if manager else str(route.get("agent_id") or "codex")
    goal_id = str(route.get("goal_id") or "")
    channel_id = str(route.get("manager_channel_id") or "") if manager else f"goal.{goal_id}"
    if (
        not isinstance(session, Mapping) or session.get("status") == "closed"
        or (not manager and session.get("goal_id") != goal_id)
        or session.get("agent_id") != agent_id or session.get("channel_id") != channel_id
    ):
        return None
    return session


def capture_topic_mention(
    route: Mapping[str, object], runtime_controller: ChatRuntimeController
) -> dict[str, object]:
    """Read the bound Session/Turn without opening or resuming an executor."""
    session = _bound_topic_session(route, runtime_controller)
    return {
        "connection_id": route.get("connection_id"),
        "session_id": route.get("session_id"),
        "binding_matches": session is not None,
        "target_turn_id": (session or {}).get("active_turn_id") or None,
    }


def _native_topic_mention(event: Mapping[str, object], identity: Mapping[str, object]) -> bool:
    if event.get("sender_type") != "user" or not event.get("sender_id") or event.get("historical_context_only"):
        return False
    expected = {str(identity.get(key) or "") for key in ("bot_app_id", "bot_open_id")} - {""}
    mentions = event.get("mentions")
    return bool(expected and isinstance(mentions, list) and any(
        isinstance(item, Mapping) and lark_provider_mention_identities(item).intersection(expected)
        for item in mentions
    ))


def _route_native_mention(event: Mapping[str, object], route: Mapping[str, object],
                          target_payload: Mapping[str, object]) -> bool:
    if route.get("ingress_mode") not in {"live_steering", "session_queue", "async_inbox"}:
        return False
    if (route.get("conversation_kind") == "manager"
            and route.get("authority_mode") != ManagerAuthorityMode.TURN_AUTHORIZED.value):
        return False
    target = goal_channel_target_for_name(target_payload, str(route.get("target_ref") or "")) or {}
    identity = target.get("identity")
    return isinstance(identity, Mapping) and _native_topic_mention(event, identity)


def _steering_reply(route: Mapping[str, object], decision: Mapping[str, object]) -> dict[str, object]:
    receipt = _session_turn_effect(route)
    if decision["action"] == "delivered":
        return {"response_text": "已送达当前回合。", "effect_receipt": receipt}
    receipt["effect_kind"] = ExternalEffectKind.NO_FOLLOW_UP.value
    if decision["action"] == "unresolved":
        return {"response_text": "送达状态待核实。原消息保留在 Lark。", "effect_receipt": receipt}
    text = {
        "queue_steering_attachments_unavailable": "本轮调整仅支持文字。原消息和附件保留在 Lark。",
        "live_steering_turn_mismatch": "接收时的回合已结束或切换。原消息保留在 Lark。",
        "live_steering_turn_not_started": "接收时的回合尚未就绪。原消息保留在 Lark。",
        "live_steering_requires_active_turn": "接收消息时没有活动回合。原消息保留在 Lark。",
    }.get(str(decision.get("error_code")), "当前会话无法调整本轮。原消息保留在 Lark。")
    return {"response_text": text, "effect_receipt": receipt}


def answer_lark_goal_topic(
    *,
    route: Mapping[str, object],
    text: str,
    work_dir: str | Path,
    objective: str,
    runtime_controller: ChatRuntimeController,
) -> str | Mapping[str, object]:
    """Deliver one Topic message using its exact Agent ingress contract."""
    goal_id = _str_field(route, "goal_id")
    ingress_mode = _str_field(route, "ingress_mode", "direct_session")
    session_id = _str_field(route, "session_id")
    manager = route.get("conversation_kind") == "manager"
    agent_id = manager_turn_executor(runtime_controller) if manager else _str_field(route, "agent_id", "codex")
    expected_channel = _str_field(route, "manager_channel_id") if manager else f"goal.{goal_id}"
    if manager:
        objective = MANAGER_AGENT_OBJECTIVE
    resolved_work_dir = Path(work_dir).expanduser().resolve()
    reply_context = (
        str(conversation_reply_context(dict(route))["context_text"])
        if route.get("parent_id") or route.get("thread_context") else ""
    )
    if manager:
        message = manager_message(text, route.get("context_materials"), reply_context=reply_context)
    else:
        message = (
            "这是来自已绑定 Lark Goal Topic 的用户消息。请直接回答当前问题；"
            "任何 Goal、Todo 或其他持久状态修改只生成预览，等待用户在 LoopX 明确确认后应用。"
            + ("\n\n" + reply_context if reply_context else "")
            + "\n\n用户消息：" + str(text or "").strip()
        )
    client_turn_id = "lark." + _opaque_digest(route.get("message_id"), route.get("topic_root_message_id"))
    native_mention = route.get("native_mention") is True
    if native_mention:
        from ...capabilities.native_chat.queued_admission import deliver_native_mention

        raw_capture = route.get("mention_capture")
        capture = raw_capture if isinstance(raw_capture, Mapping) else capture_topic_mention(route, runtime_controller)
        current = _bound_topic_session(route, runtime_controller)
        binding_matches = (
            current is not None and capture.get("binding_matches") is True
            and capture.get("session_id") == session_id
            and capture.get("connection_id") == route.get("connection_id")
            and ingress_mode in {"live_steering", "session_queue"}
        )
        if manager and binding_matches and current is not None and capture.get("target_turn_id"):
            from ...chat_manager_context import manager_authorization_scope_is_current

            binding_matches = manager_authorization_scope_is_current(
                runtime_controller.registry_path, runtime_controller.manager_scope_resolver, dict(current),
                current.get("manager_authorization_scope_id"),
                runtime_root=runtime_controller.coordination_runtime_root,
            )
        body = text
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, Mapping) and isinstance(payload.get("text"), str):
            body = payload["text"]
        target_turn_id = capture.get("target_turn_id")
        decision = deliver_native_mention(
            runtime_controller, session_id=session_id, ingress_id=client_turn_id,
            target_turn_id=str(target_turn_id) if isinstance(target_turn_id, str) and target_turn_id else None,
            binding_matches=binding_matches,
            text_only=route.get("native_mention_text_only") is True,
            may_deliver_new=route.get("native_mention_fresh") is True,
            steering_text=(reply_context + "\n\n" if reply_context else "") + body,
            queued_text=message, origin="lark",
        )
        if decision["action"] != "enqueue":
            return _steering_reply(route, decision)
        ingress_mode = "session_queue"

    if ingress_mode in {"live_steering", "session_queue"}:
        if _bound_topic_session(route, runtime_controller) is None:
            if manager and manager_session_requires_executor_rebind(
                runtime_controller.store.load_session(session_id),
                expected_channel=expected_channel, agent_id=agent_id,
            ):
                raise LarkGoalTopicTurnFailed("manager_channel_executor_rebind_required", _session_turn_effect(route))
            raise RuntimeError("bound Agent session is unavailable or no longer matches")
        if ingress_mode == "live_steering":
            live_capture = route.get("live_turn_capture")
            if isinstance(live_capture, Mapping):
                if (live_capture.get("binding_matches") is not True
                        or live_capture.get("session_id") != session_id
                        or live_capture.get("connection_id") != route.get("connection_id")):
                    return _steering_reply(route, {"action": "reject", "error_code": "live_steering_session_not_attached"})
                target_turn_id = live_capture.get("target_turn_id")
                if not isinstance(target_turn_id, str) or not target_turn_id:
                    return _steering_reply(route, {"action": "reject", "error_code": "live_steering_requires_active_turn"})
                try:
                    turn, _created = runtime_controller.steer_active_turn(
                        session_id=session_id, client_ingress_id=client_turn_id,
                        message=message, expected_turn_id=target_turn_id, origin="lark",
                    )
                except Exception:
                    # A lost provider response may follow a successful send.
                    # Read the Chat owner's receipt without sending again.
                    from ...chat_runtime import STEERING_NOT_DELIVERED_CODES

                    receipt = runtime_controller.store.steering_receipt(session_id, client_turn_id) or {}
                    code = receipt.get("error_code")
                    if (
                        receipt.get("expected_turn_id") == target_turn_id and receipt.get("message") == message
                        and receipt.get("status") == "failed" and code in STEERING_NOT_DELIVERED_CODES
                    ):
                        return _steering_reply(route, {"action": "reject", "error_code": code})
                    return _steering_reply(route, {"action": "unresolved"})
            else:
                turn, _created = runtime_controller.steer_active_turn(
                    session_id=session_id, client_ingress_id=client_turn_id, message=message,
                )
        else:
            if manager and route.get("source_sender_id") and hasattr(runtime_controller.store, "root"):
                from ...capabilities.manager_context import register_ingress

                register_ingress(
                    runtime_controller.store.root.parent, session_id=session_id,
                    client_turn_id=client_turn_id, channel=expected_channel,
                    sender_id=str(route["source_sender_id"]), message=message,
                    source_id="lark:" + str(route["message_id"]),
                    source_message=(
                        (reply_context + "\n\nUser message:\n" if reply_context else "") + str(text or "").strip()
                    ),
                )
            turn, _created = runtime_controller.enqueue_turn(
                session_id=session_id, client_turn_id=client_turn_id, message=message,
                work_dir=resolved_work_dir, objective=str(objective or goal_id), origin="lark",
            )
            if native_mention:
                runtime_controller.store.update_ingress_receipt(session_id, client_turn_id, status="delivered")
    else:
        # Compatibility path for bindings created before the three ingress modes.
        channel_id = "lark." + _opaque_digest(
            route.get("app_ref"), route.get("target_ref"), route.get("topic_root_message_id"),
        )
        session, _resumed = runtime_controller.open_session(
            goal_id=goal_id, agent_id=agent_id, work_dir=resolved_work_dir,
            objective=str(objective or goal_id), mode="resume_latest",
            channel_id=channel_id, agent_goal_id=goal_id,
        )
        session_id = str(session["session_id"])
        turn, _created = runtime_controller.submit_turn(
            session_id=session_id, client_turn_id=client_turn_id, message=message,
            work_dir=resolved_work_dir, objective=str(objective or goal_id),
        )
    completed = runtime_controller.wait_for_turn(session_id=session_id, turn_id=str(turn["turn_id"]))
    if completed.get("status") != "completed":
        if completed.get("status") not in {"failed", "timed_out", "interrupted"}:
            raise RuntimeError("Lark Goal Topic turn has no terminal receipt")
        raise LarkGoalTopicTurnFailed(str(completed.get("error_code") or ""), _session_turn_effect(route))
    response = completed.get("response")
    reply_text = _str_field(response, "message") if isinstance(response, Mapping) else ""
    if not reply_text.strip():
        raise RuntimeError("Lark Goal Topic turn returned no message")
    if not manager:
        return reply_text
    proposal_ids = proposal_ids_after_turn(runtime_controller, session_id=session_id, turn_id=str(turn["turn_id"]))
    return {"response_text": reply_text, "proposal_ids": proposal_ids} if proposal_ids else reply_text


def _inbox_config(
    *, runtime_root: Path, route: Mapping[str, object], target_payload: Mapping[str, object],
) -> tuple[Path, str]:
    target = goal_channel_target_for_name(target_payload, _str_field(route, "target_ref"))
    if target is None:
        raise ValueError("the routed Lark target is unavailable")
    channel = _as_mapping(target.get("channel"))
    identity = _as_mapping(target.get("identity"))
    chat_id = _str_field(channel, "chat_id")
    profile = _str_field(route, "app_ref")
    topic_root = _str_field(route, "topic_root_message_id")
    digest = topic_inbox_key(profile, chat_id, topic_root)
    config_ref = f".loopx/config/lark-goal-topics/{digest}.json"
    config_path = runtime_root / config_ref
    payload = {
        "schema_version": "lark_event_inbox_config_v0",
        "enabled": True,
        "inbox_dir": f".loopx/inbox/lark-goal-topics/{digest}",
        "capture_scope": "configured_chat_all",
        "topic_root_message_id": topic_root,
        "material_review": {
            "enabled": route.get("conversation_kind") == "manager",
            "drain_limit": MANAGER_CONTEXT_ITEM_LIMIT,
        },
        "reply": {
            "enabled": True,
            "sender_profile": profile,
            "sender_identity": "bot",
            "bot_display_name": _str_field(identity, "bot_display_name", profile),
            "bot_app_id": _str_field(identity, "bot_app_id"),
            "bot_open_id": _str_field(identity, "bot_open_id"),
            "chat_id": chat_id,
            "placement_policy": "source_context",
            "editorial_style": "bullet_points_preferred",
            "received_reaction_policy": "retain" if route.get("conversation_kind") == "manager" else "transient",
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private_json_atomic(config_path, payload)
    return config_path, config_ref


def process_lark_goal_topic_event(
    *,
    target_payload: Mapping[str, object],
    binding_payloads: Mapping[str, Mapping[str, object]],
    event: Mapping[str, object],
    runtime_root: str | Path,
    goal_contexts: Mapping[str, Mapping[str, object]] | None = None,
    answer: Answer,
    reply_runner: CommandRunner,
    provider_runner: ProviderRunner | None = None,
    proposal_deliverer: ProposalDeliverer | None = None,
    mention_capture: Mapping[str, object] | None = None,
    live_turn_capture: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Single-flight a manager or live mention through ingress, reply and ACK.

    The inbox and delivery receipt make sequential retries safe, but two
    listeners can otherwise both observe an absent receipt and generate two
    different answers before either writes it. The lock is per source message
    and spans the entire effect, including provider readback and inbox ACK.
    """

    decision = decide_lark_topic_event(
        target_payload=target_payload, binding_payloads=binding_payloads,
        event=event, runtime_root=runtime_root,
    )
    raw_route = decision.get("route")
    route = dict(raw_route) if isinstance(raw_route, Mapping) else None
    native_mention = route is not None and _route_native_mention(event, route, target_payload)
    if mention_capture is not None and not native_mention:
        return {"ok": True, "status": "ignored", "reason": "mention_binding_changed"}
    if live_turn_capture is not None:
        if route is None or route.get("ingress_mode") != "live_steering" or native_mention:
            return {"ok": True, "status": "ignored", "reason": LarkTopicEventDecisionReason.BINDING_UNAVAILABLE.value}
        route = {**route, "live_turn_capture": live_turn_capture}
        decision = {**decision, "route": route}
    if route is not None and native_mention:
        route = {
            **route,
            "native_mention": True,
            "native_mention_text_only": event.get("message_type") == "text" and not event.get("attachment_count"),
            "mention_capture": mention_capture,
        }
        decision = {**decision, "route": route}
    lock: AbstractContextManager[object] = nullcontext()
    if route is not None and (
        native_mention or (
            route.get("conversation_kind") == "manager"
            and route.get("authority_mode") == ManagerAuthorityMode.TURN_AUTHORIZED.value
        )
    ):
        message_id = _str_field(route, "message_id")
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            raise ValueError("manager route has an invalid message id")
        lock_dir = Path(runtime_root).expanduser().resolve() / ".loopx/locks/lark-manager"
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(lock_dir, 0o700)
        lock = exclusive_file_lock(lock_dir / message_id, timeout_seconds=960, operation="lark_manager_message")
    with lock:
        return _process_lark_goal_topic_event(
            target_payload=target_payload, event=event, runtime_root=runtime_root,
            goal_contexts=goal_contexts, answer=answer, reply_runner=reply_runner,
            provider_runner=provider_runner, proposal_deliverer=proposal_deliverer, decision=decision,
        )


def _process_lark_goal_topic_event(
    *,
    target_payload: Mapping[str, object],
    event: Mapping[str, object],
    runtime_root: str | Path,
    goal_contexts: Mapping[str, Mapping[str, object]] | None,
    answer: Answer,
    reply_runner: CommandRunner,
    provider_runner: object | None,
    proposal_deliverer: ProposalDeliverer | None,
    decision: Mapping[str, object],
) -> dict[str, object]:
    """Persist, answer, reply, and ACK the already-routed Topic event."""

    raw_route = decision.get("route")
    if not isinstance(raw_route, Mapping):
        return {
            "ok": True,
            "status": "ignored",
            "reason": _str_field(decision, "reason", LarkTopicEventDecisionReason.BINDING_UNAVAILABLE.value),
        }
    route: dict[str, object] = dict(raw_route)
    goal_id = route["goal_id"]
    ingress_mode = _str_field(route, "ingress_mode", "direct_session")
    if ingress_mode == "async_inbox":
        context = _as_mapping(_as_mapping(goal_contexts).get(str(goal_id or "")))
        work_dir = _str_field(context, "work_dir").strip()
        config_ref = _str_field(route, "inbox_config_ref").strip()
        if not work_dir or not config_ref:
            return _topic_error("agent_inbox_unavailable", goal_id)
        root = Path(work_dir).expanduser().resolve()
        config_path = Path(config_ref)
    else:
        root = Path(runtime_root).expanduser().resolve()
        config_path, config_ref = _inbox_config(runtime_root=root, route=route, target_payload=target_payload)
    canonical: dict[str, object] = {
        "schema_version": "lark_event_inbox_event_v0",
        "event_id": str(event.get("event_id") or event.get("message_id") or ""),
        "message_id": _str_field(event, "message_id"),
        "create_time": _str_field(event, "create_time"),
        "content": _str_field(event, "content"),
        "sender_type": _str_field(event, "sender_type"),
        "sender_id": _str_field(event, "sender_id"),
        "root_id": _str_field(event, "root_id"),
        "thread_id": _str_field(event, "thread_id"),
        "thread_context": event.get("thread_context"),
        "parent_id": _str_field(event, "parent_id"),
        "mentions": event.get("mentions") if isinstance(event.get("mentions"), list) else [],
        "reply_context_verified": event.get("reply_context_verified") is True,
        "reply_to_bot": event.get("reply_to_bot") is True,
        "reply_context": event.get("reply_context"),
    }
    ingest = ingest_lark_event_inbox(project=root, config_path=config_path, events=[canonical], execute=True)
    manager = route.get("conversation_kind") == "manager"
    authority_mode = parse_manager_authority_mode(route.get("authority_mode")) if manager else None
    if manager and authority_mode is None:
        return invalid_manager_authority_result(route, inbox_config_ref=config_ref)
    retention = {"discarded_count": 0, "expired_count": 0, "overflow_count": 0}
    if manager:
        _, retention = manager_context_projection(
            project=root, config_path=config_path, current_message_id=_str_field(canonical, "message_id"),
        )
    if authority_mode is ManagerAuthorityMode.CONTEXT_ONLY:
        return {
            "ok": True,
            "status": "context_only_captured" if _int_field(ingest, "accepted_count") else "context_only_already_captured",
            "reason": normalize_lark_topic_event_rejection_reason(route.get("trigger_reason")) or LarkTopicEventDecisionReason.NOT_ADDRESSED.value,
            "goal_id": goal_id,
            "inbox_config_ref": config_ref,
            "turn_authorized": False,
            "model_invoked": False,
            "external_write_performed": False,
            "context_retention_discarded_count": retention["discarded_count"],
        }
    context_sync: Mapping[str, object] | None = None
    if manager and provider_runner is not None:
        context_sync = sync_manager_context(
            project=root, config_path=config_path, target_payload=target_payload,
            target_ref=_str_field(route, "target_ref"), provider_runner=provider_runner,
        )
    message_id = str(route["message_id"])
    projection = inspect_lark_event_inbox(project=root, config_path=config_path, limit=0)
    if manager:
        projection, retention = manager_context_projection(
            project=root, config_path=config_path, current_message_id=message_id,
        )
    projection_items = _dict_list(projection.get("items"))
    if message_id not in {_str_field(item, "message_id") for item in projection_items}:
        return {"ok": True, "status": "already_acknowledged", "goal_id": goal_id, "inbox_config_ref": config_ref}
    if ingress_mode == "async_inbox":
        return {
            "ok": True, "status": "queued_for_agent", "goal_id": goal_id,
            "agent_id": route.get("agent_id"), "inbox_config_ref": config_ref,
            **({"reason": "async_inbox_requires_bound_session"} if route.get("native_mention") else {}),
        }
    # Receipt ACK is visible while the synchronous manager is reasoning. The
    # private reaction ledger makes retries idempotent. Manager received ACKs
    # remain visible; final reply only clears transient processing indicators.
    # A cosmetic reaction failure must not suppress the actual answer.
    received_reaction = None
    if manager:
        profile = _str_field(route, "app_ref")
        try:
            received_reaction = ensure_lark_event_inbox_received_reaction(
                project=root, config_path=config_path, event=canonical,
                create_reaction=lambda mid, emoji: _create_reaction(runner=reply_runner, profile=profile, message_id=mid, emoji_type=emoji),
                delete_reaction=lambda mid, rid: _delete_reaction(runner=reply_runner, profile=profile, message_id=mid, reaction_id=rid),
            )
        except (OSError, ValueError):
            received_reaction = {"ok": False, "status": "reaction_state_unavailable"}
        if not received_reaction.get("ok"):
            logging.getLogger(__name__).warning("Lark manager received reaction was not verified")
    # Sender provenance comes from the provider event, never the model response.
    # Context-only messages stay visibly non-authoritative inside the next
    # addressed manager Turn and never call the model on their own.
    context_materials = manager_context_materials(projection, current_message_id=message_id) if manager else []
    captured_event = next(item for item in projection_items if item.get("message_id") == message_id)
    if route.get("native_mention") and (
        not captured_event.get("target_mention_count") or captured_event.get("historical_context_only")
        or captured_event.get("sender_id") != canonical["sender_id"]
    ):
        return _topic_error("native_mention_proof_changed", goal_id)
    route = {
        **route,
        "source_sender_id": _str_field(canonical, "sender_id"),
        "source_conversation_id": _str_field(event, "chat_id"),
        # The immutable captured event owns reply ancestry on delivery retries.
        "parent_id": captured_event.get("parent_id", ""),
        "reply_context": captured_event.get("reply_context"),
        "root_id": captured_event.get("root_id", ""),
        "thread_id": captured_event.get("thread_id", ""),
        "thread_context": captured_event.get("thread_context"),
        **({"native_mention_fresh": _int_field(ingest, "accepted_count") > 0} if route.get("native_mention") else {}),
        **({"context_materials": context_materials} if context_materials else {}),
    }
    delivery_path: Path | None = None
    delivery_state: dict[str, object] | None = None
    saved_response_reused = False
    if manager:
        try:
            delivery_path, delivery_state = _load_manager_delivery(project=root, config_path=config_path, event=canonical)
        except (OSError, ValueError):
            return _topic_error("reply_delivery_state_invalid", goal_id, inbox_config_ref=config_ref, source_acknowledged=False)
        if delivery_state is not None and delivery_state.get("status") == "acknowledged":
            return _topic_error("reply_delivery_state_invalid", goal_id, inbox_config_ref=config_ref, source_acknowledged=False)
        try:
            route = restore_manager_context_route(route, projection, current_message_id=message_id, delivery_state=delivery_state)
        except ValueError:
            return unavailable_manager_context_result(route, inbox_config_ref=config_ref)
        raw_materials = route.get("context_materials")
        if isinstance(raw_materials, list):
            context_materials = [
                {str(k): str(v) for k, v in item.items()} for item in raw_materials if isinstance(item, Mapping)
            ]

    failure_code: str | None = None
    effect_receipt: Mapping[str, object] | None = None
    proposal_ids: list[str] = []
    answer_completed = False
    if delivery_state is not None:
        saved_response_reused = True
        answer_completed = True
        reply_text = str(delivery_state["delivery_text"])
        content_format = str(delivery_state["content_format"])
        saved_effect = delivery_state.get("effect_receipt")
        effect_receipt = saved_effect if isinstance(saved_effect, Mapping) else None
        saved_failure = delivery_state.get("failure_code")
        failure_code = str(saved_failure) if saved_failure else None
        proposal_ids = _str_list(delivery_state.get("proposal_ids"))
    else:
        try:
            answer_result = answer(route, str(canonical["content"]))
            answer_completed = True
        except LarkGoalTopicTurnFailed as exc:
            if not manager:
                raise
            failure_code, failure_text = _manager_failure_reply(exc)
            answer_result = {"response_text": failure_text, "effect_receipt": exc.effect_receipt}
        except Exception as exc:
            # A synchronous manager route must leave a user-visible, bounded
            # receipt even when the worker raises an untyped exception.  The
            # exception itself may contain private provider details, so only its
            # type is logged and the public reply uses the generic failure label.
            if not manager:
                raise
            logging.getLogger(__name__).warning("Lark manager answer failed with %s", type(exc).__name__)
            failure_code, failure_text = _manager_failure_reply(exc)
            answer_result = {"response_text": failure_text, "effect_receipt": _session_turn_effect(route)}
        if isinstance(answer_result, Mapping):
            reply_text = _str_field(answer_result, "response_text").strip()
            candidate_receipt = answer_result.get("effect_receipt")
            effect_receipt = candidate_receipt if isinstance(candidate_receipt, Mapping) else None
            proposal_ids = _str_list(answer_result.get("proposal_ids"), skip_empty=True)
        else:
            reply_text = str(answer_result or "").strip()
        content_format = "markdown" if manager else "text"

    raw_connector = route.get("connector")
    connector = raw_connector if isinstance(raw_connector, Mapping) else None
    if not reply_text:
        answer_completed = False
        if not manager:
            return _topic_error("answer_empty", goal_id, inbox_config_ref=config_ref)
        # Empty model output is a terminal, non-replayable failure for a
        # manager request.  Reply through the same inbox path so the
        # source is ACKed only after provider verification.
        failure_code = "answer_empty"
        reply_text = (
            "已收到你的消息，但本次没有生成可发送的完整答复。"
            "请求不会自动重放；请在 LoopX 管家会话查看状态或重新发起。"
        )
        effect_receipt = effect_receipt or _session_turn_effect(route)
    rich_text_repairs: list[dict[str, object]] = []
    if manager:
        # The reader must not receive a one-line answer whose bullet list is
        # still escaped, nor raw braces left by an unresolved template.  Repair
        # those defects before the first send: the strict validator keeps
        # owning unsafe markup, and a repaired markdown answer stays markdown
        # instead of degrading to plain text.
        reply_text, repairs = repair_manager_reply_text(reply_text)
        rich_text_repairs = [dict(item) for item in repairs]
        reply_text = present_lark_conversation_text(reply_text)
    if manager and delivery_state is None:
        assert delivery_path is not None
        delivery_state = _pending_manager_delivery(
            event=canonical, text=reply_text, content_format=content_format,
            effect_receipt=effect_receipt, failure_code=failure_code,
            context_material_ids=[item["message_id"] for item in context_materials],
            proposal_ids=proposal_ids,
        )
        if rich_text_repairs:
            delivery_state["rich_text_repairs"] = rich_text_repairs
        try:
            _write_manager_delivery(delivery_path, delivery_state)
        except OSError:
            return _topic_error("reply_delivery_state_unavailable", goal_id, inbox_config_ref=config_ref, source_acknowledged=False)
    if connector is not None:
        effect_decision = decide_external_event_ack(
            event_id=str(canonical["event_id"]), effect_receipt=effect_receipt,
            response_policy=ExternalResponsePolicy.NO_RESPONSE.value,
        )
        if not effect_decision["effect_ready"]:
            return _topic_error("durable_effect_required", goal_id, inbox_config_ref=config_ref, ack_decision=effect_decision)

    if delivery_state is not None and delivery_state.get("status") == "sent_verified":
        reply: dict[str, object] = {
            "ok": True,
            "status": "sent_verified",
            "idempotency_key": delivery_state.get("reply_idempotency_key"),
            "external_write_performed": delivery_state.get("external_write_performed") is True,
            "verification_performed": True,
            "reply_verified": True,
        }
    else:
        if delivery_state is not None:
            assert delivery_path is not None
            _persist_delivery(delivery_path, delivery_state, attempt_count=_int_field(delivery_state, "attempt_count") + 1)
        try:
            reply = reply_lark_event_inbox(
                project=root, config_path=config_path, message_id=message_id, text=reply_text,
                content_format=content_format, execute=True, runner=reply_runner,
                # Ordinary answers use the provider capacity. The shared Inbox
                # sender splits oversized rich replies without a text downgrade.
                short_message_limit=None,
            )
        except LarkOutboundTextError:
            if not manager:
                raise
            # Length is handled by the shared rich-post splitter. Invalid
            # presentation remains an explicit failure, never a text downgrade.
            assert delivery_state is not None and delivery_path is not None
            _persist_delivery(delivery_path, delivery_state, last_delivery_status="format_unrepresentable")
            return _topic_error(
                "reply_delivery_pending", goal_id, inbox_config_ref=config_ref,
                source_acknowledged=False, reason="reply_format_invalid", format_degraded=False,
            )
    if manager and reply.get("content_format") in {"markdown", "text"}:
        content_format = str(reply["content_format"])
    if manager and delivery_state is not None:
        delivery_state.update(manager_part_delivery_readback(reply))
    if not reply.get("ok"):
        reply_status = _str_field(reply, "status", "reply_failed")
        if manager:
            assert delivery_state is not None and delivery_path is not None
            _persist_delivery(
                delivery_path, delivery_state, last_delivery_status=reply_status,
                last_blocker=_str_field(reply, "blocker", "reply_delivery_unverified"),
            )
        return _topic_error(
            "reply_delivery_pending" if manager else reply_status,
            goal_id,
            inbox_config_ref=config_ref,
            source_acknowledged=False if manager else None,
            blocker=reply.get("blocker"),
            **(
                {
                    "delivery_status": reply_status,
                    "format_degraded": bool(delivery_state and delivery_state.get("format_degraded")),
                    "rich_text_repairs": _dict_list((delivery_state or {}).get("rich_text_repairs")),
                    **manager_part_delivery_readback(reply),
                    "delivery_notice_sent": reply.get("delivery_notice_sent") is True,
                }
                if manager else {}
            ),
        )
    if manager:
        assert delivery_state is not None and delivery_path is not None
        try:
            _persist_delivery(
                delivery_path, delivery_state,
                status="sent_verified", delivery_text=reply_text,
                delivery_digest=_manager_delivery_text_digest(reply_text),
                content_format=content_format,
                reply_idempotency_key=reply.get("idempotency_key"),
                external_write_performed=(reply.get("external_write_performed") is True),
                verification_performed=(reply.get("verification_performed") is True),
                reply_verified=(reply.get("reply_verified") is True),
                last_delivery_status=_str_field(reply, "status", "sent_verified"),
            )
        except OSError:
            return _topic_error("reply_delivery_receipt_unavailable", goal_id, inbox_config_ref=config_ref, source_acknowledged=False)
        proposal_delivery_status = settle_team_plan_proposal_delivery(
            delivery_state=delivery_state, delivery_path=delivery_path, route=route,
            proposal_ids=proposal_ids, proposal_deliverer=proposal_deliverer,
        )
        if proposal_delivery_status is not None:
            return _topic_error(proposal_delivery_status, goal_id, inbox_config_ref=config_ref, source_acknowledged=False)
    if connector is not None:
        ack_decision = decide_external_event_ack(
            event_id=str(canonical["event_id"]),
            effect_receipt=effect_receipt,
            response_policy=_str_field(connector, "response_policy"),
            response_receipt=build_external_event_response_receipt(
                event_id=str(canonical["event_id"]),
                external_write_performed=(reply.get("external_write_performed") is True),
                verification_performed=(reply.get("verification_performed") is True),
                response_verified=reply.get("reply_verified") is True,
            ),
        )
        if not ack_decision["ack_allowed"]:
            return _topic_error(str(ack_decision["reason"]), goal_id, inbox_config_ref=config_ref, ack_decision=ack_decision)
    context_settled_count = settle_manager_context(
        project=root, config_path=config_path,
        materials=context_materials if answer_completed else [],
    )
    acknowledge_lark_event_inbox(project=root, config_path=config_path, message_ids=[message_id], execute=True)
    if manager:
        assert delivery_state is not None and delivery_path is not None
        delivery_state.pop("delivery_text", None)
        delivery_state.pop("effect_receipt", None)
        try:
            _persist_delivery(delivery_path, delivery_state, status="acknowledged")
        except OSError:
            logging.getLogger(__name__).warning("Lark manager delivery receipt cleanup could not be persisted")

    return {
        "ok": failure_code is None,
        "status": "processing_failed" if failure_code else "replied_and_acknowledged",
        **({"reason": failure_code, "failure_reply_verified": True, "source_acknowledged": True} if failure_code else {}),
        "received_reaction_status": (received_reaction or {}).get("status"),
        **(
            {
                "saved_response_reused": saved_response_reused,
                "format_degraded": bool(delivery_state.get("format_degraded")),
                "rich_text_repairs": _dict_list(delivery_state.get("rich_text_repairs")),
                **manager_part_delivery_readback(delivery_state),
            }
            if manager and delivery_state is not None else {}
        ),
        "context_material_count": len(context_materials),
        "context_settled_count": context_settled_count,
        "context_retention_discarded_count": retention["discarded_count"],
        "context_sync_status": _str_field(context_sync, "status", "unknown") if context_sync is not None else "not_attempted",
        "goal_id": goal_id,
        "inbox_config_ref": config_ref,
    }


# Preserve the established import path while keeping worker lifecycle out of
# the already hot message-processing module.
from .goal_topic_runtime_service import (  # noqa: E402,F401
    LarkGoalTopicRuntimeService,
)
