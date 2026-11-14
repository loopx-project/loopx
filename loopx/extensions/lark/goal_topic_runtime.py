"""Runtime bridge from bound Lark Goal Topics into the existing Inbox path."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

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
    MESSAGE_ID_PATTERN,
    acknowledge_lark_event_inbox,
    ingest_lark_event_inbox,
    inspect_lark_event_inbox,
)
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
    proposal_ids_after_turn,
    settle_team_plan_proposal_delivery,
    start_team_plan_review_callback_stream,
)

if TYPE_CHECKING:
    from ...chat_runtime import ChatRuntimeController


Answer = Callable[[Mapping[str, Any], str], str | Mapping[str, Any]]
SnapshotProvider = Callable[[], Mapping[str, Any]]
SimpleRunner = Callable[[list[str]], Mapping[str, Any]]
ProcessFactory = Callable[[list[str]], Any]
HealthSink = Callable[[Mapping[str, Any]], None]
ProposalDeliverer = Callable[
    [Mapping[str, Any], list[str]], Mapping[str, Any]
]
ReviewCallbackHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]
EventLane = tuple[str, ...]
CapturedEventLane = tuple[EventLane, dict[str, object] | None, TopicIntakeRoute | None]


class LarkGoalTopicTurnFailed(RuntimeError):
    """A terminal runtime receipt, without copying arbitrary upstream details."""

    def __init__(self, error_code: str, effect_receipt: Mapping[str, Any]) -> None:
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


def _active_group_profile_configs(snapshot: Mapping[str, object]) -> dict[str, dict[str, str]]:
    target_payload = snapshot.get("target_payload")
    target_payload = target_payload if isinstance(target_payload, Mapping) else {}
    binding_payloads = snapshot.get("binding_payloads")
    binding_payloads = binding_payloads if isinstance(binding_payloads, Mapping) else {}
    profiles: dict[str, dict[str, str]] = {}
    for goal_id, payload in binding_payloads.items():
        if not isinstance(payload, Mapping):
            continue
        for binding in bindings_for_goal(payload, str(goal_id)):
            if binding.get("enabled") is not True:
                continue
            target = goal_channel_target_for_name(
                target_payload,
                str(binding.get("target_ref") or ""),
            )
            if target is None or target.get("enabled") is not True:
                continue
            identity = target.get("identity")
            identity = identity if isinstance(identity, Mapping) else {}
            profile = str(identity.get("sender_profile") or "").strip()
            if not profile:
                continue
            profiles.setdefault(
                profile,
                {
                    "cli_bin": str(identity.get("cli_bin") or "lark-cli"),
                    "bot_app_id": str(identity.get("bot_app_id") or ""),
                },
            )
    return profiles


def _active_profile_configs(snapshot: Mapping[str, object]) -> dict[str, dict[str, str]]:
    from .conversation_identity import identity_ref

    profiles = _active_group_profile_configs(snapshot)
    private = snapshot.get("private_profiles")
    for profile, config in (private.items() if isinstance(private, Mapping) else []):
        if profile in profiles:
            stored_app_id = profiles[profile].get("bot_app_id")
            app_id = stored_app_id or config.get("bot_app_id")
            if (config.get("audience", "owner") != "owner" or not app_id
                    or identity_ref(app_id) != config.get("provider_ref")):
                raise ValueError("shared listener requires the same verified App and separate audiences")
            profiles[profile] = {**profiles[profile], **dict(config)}
            if not stored_app_id:
                # Keep the pre-upgrade group lease until its original owner
                # exits. Verified private discovery supplies identity, not a
                # reason to start a second consumer under a new App key.
                profiles[profile]["consumer_ref"] = hashlib.sha256(profile.encode("utf-8")).hexdigest()[:32]
        else:
            profiles[profile] = dict(config)
    return profiles


def _native_profile_event(snapshot: Mapping[str, Any], profile: str, event: Mapping[str, object]) -> bool:
    """Select a transport lane only; its original owner still admits the input.

    An owner-private binding may share one consumer with legacy group routes.
    A private rejection never falls through to a group authority or vice versa.
    """
    config = dict(snapshot.get("private_profiles") or {}).get(profile)
    return isinstance(config, Mapping) and not (
        config.get("audience", "owner") == "owner" and event.get("chat_type") == "group"
        and profile in _active_group_profile_configs(snapshot)
    )


def _default_simple_runner(args: list[str]) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {"returncode": 1, "stdout": "", "stderr": ""}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _default_process_factory(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1,
    )


def _target_for_profile_chat(
    target_payload: Mapping[str, Any],
    *,
    profile: str,
    chat_id: str,
    bot_app_id: str = "",
    active_target_refs: set[str] | None = None,
    root_id: str = "",
    binding_payloads: Mapping[str, object] | None = None,
) -> tuple[str, Mapping[str, Any]] | None:
    targets = target_payload.get("targets")
    targets = targets if isinstance(targets, Mapping) else {}
    candidates: list[tuple[str, Mapping[str, Any]]] = []
    for target_ref, target in targets.items():
        if active_target_refs is not None and str(target_ref) not in active_target_refs:
            continue
        if not isinstance(target, Mapping) or target.get("enabled") is not True:
            continue
        channel = target.get("channel")
        channel = channel if isinstance(channel, Mapping) else {}
        identity = target.get("identity")
        identity = identity if isinstance(identity, Mapping) else {}
        same_app = (
            bool(bot_app_id) and str(identity.get("bot_app_id") or "") == bot_app_id
        )
        if str(channel.get("chat_id") or "") == chat_id and (
            same_app or str(identity.get("sender_profile") or "") == profile
        ):
            candidates.append((str(target_ref), target))
    if len(candidates) == 1:
        return candidates[0]
    bindings = binding_payloads if isinstance(binding_payloads, Mapping) else {}
    if root_id:
        rooted = [
            candidate
            for candidate in candidates
            if root_id in _topic_roots_for_target(bindings, target_ref=candidate[0])
        ]
        if len(rooted) == 1:
            return rooted[0]
        if rooted:
            return None
    managers = [
        candidate
        for candidate in candidates
        if has_manager_binding(bindings, candidate[0])
    ]
    return managers[0] if len(managers) == 1 else None


def _topic_roots_for_target(
    binding_payloads: Mapping[str, Any], *, target_ref: str
) -> list[str]:
    roots: list[str] = []
    for goal_id, payload in binding_payloads.items():
        if not isinstance(payload, Mapping):
            continue
        bindings = bindings_for_goal(payload, str(goal_id))
        roots.extend(_topic_roots_for_bindings(bindings, target_ref=target_ref))
    return roots


def _topic_roots_for_bindings(
    bindings: list[Mapping[str, Any]], *, target_ref: str
) -> list[str]:
    roots: list[str] = []
    for binding in bindings:
        if binding.get("enabled") is not True:
            continue
        if str(binding.get("target_ref") or "") != target_ref:
            continue
        topic = binding.get("topic")
        topic = topic if isinstance(topic, Mapping) else {}
        channel = binding.get("channel")
        channel = channel if isinstance(channel, Mapping) else {}
        root_id = str(
            topic.get("root_message_id") or channel.get("pinned_message_id") or ""
        )
        if MESSAGE_ID_PATTERN.fullmatch(root_id):
            roots.append(root_id)
    return roots


def _binding_payloads_for_target(
    binding_payloads: Mapping[str, Any], *, target_ref: str
) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, Mapping[str, Any]] = {}
    for goal_id, payload in binding_payloads.items():
        if not isinstance(payload, Mapping):
            continue
        if any(
            binding.get("enabled") is True
            and str(binding.get("target_ref") or "") == target_ref
            for binding in bindings_for_goal(payload, str(goal_id))
        ):
            selected[str(goal_id)] = payload
    return selected


def _event_payloads(stdout: Any) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
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
    snapshot: Mapping[str, Any],
    event: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], dict[str, Mapping[str, Any]], dict[str, Any]] | None, str
]:
    """Select the existing profile/target/Topic scope; never grant turn authority."""

    profile_config = _active_profile_configs(snapshot).get(profile)
    if profile_config is None:
        return None, "inactive"
    target_payload = snapshot.get("target_payload")
    target_payload = target_payload if isinstance(target_payload, Mapping) else {}
    binding_payloads = snapshot.get("binding_payloads")
    binding_payloads = binding_payloads if isinstance(binding_payloads, Mapping) else {}
    active_target_refs = {
        str(binding.get("target_ref") or "")
        for goal_id, payload in binding_payloads.items()
        if isinstance(payload, Mapping)
        for binding in bindings_for_goal(payload, str(goal_id))
        if binding.get("enabled") is True
    }
    chat_id = str(event.get("chat_id") or "")
    target_match = _target_for_profile_chat(
        target_payload,
        profile=profile,
        chat_id=chat_id,
        bot_app_id=str(profile_config.get("bot_app_id") or ""),
        active_target_refs=active_target_refs,
        root_id=str(event.get("root_id") or ""),
        binding_payloads=binding_payloads,
    )
    if target_match is None:
        return None, "target_unmatched"
    target_ref, _target = target_match
    routed_event = dict(event)
    root_id = str(routed_event.get("root_id") or "")
    if not MESSAGE_ID_PATTERN.fullmatch(root_id) and not has_manager_binding(
        binding_payloads, target_ref
    ):
        candidate_roots = _topic_roots_for_target(
            binding_payloads,
            target_ref=target_ref,
        )
        if len(candidate_roots) > 1:
            return None, "topic_context_ambiguous"
        if not candidate_roots:
            return None, "topic_context_missing"
        routed_event["root_id"] = candidate_roots[0]
    return (
        target_payload,
        _binding_payloads_for_target(binding_payloads, target_ref=target_ref),
        routed_event,
    ), "matched"


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
    snapshot: Mapping[str, Any],
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

    private_config = dict(snapshot.get("private_profiles") or {}).get(profile)
    if isinstance(private_config, Mapping) and _native_profile_event(snapshot, profile, event):
        from .conversation_identity import lark_private_source

        try:
            private_source = lark_private_source(
                provider_ref=private_config["provider_ref"], event=event
            )
        except (KeyError, TypeError, ValueError):
            pass  # Malformed hints retain the conservative unmatched lane.
        else:
            # Native profiles bypass legacy Goal routing at admission too.
            # Use the same App/chat/topic (or private sender) source shape for
            # scheduling, never a grant or a persisted Session selection. Core
            # still verifies the provider and acquires its fresh source fence.
            return result(("private." + private_source["source_ref"],))

    scoped, _status = _profile_event_route(
        profile=profile, snapshot=snapshot, event=event
    )
    if scoped is not None:
        targets, bindings, routed_event = scoped
        decision = decide_lark_topic_event(
            target_payload=targets,
            binding_payloads=bindings,
            event=routed_event,
            runtime_root=runtime_root,
        )
        route = decision.get("route") or {}
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
    snapshot: Mapping[str, Any],
    runtime_root: str | Path,
    answer: Answer,
    consume_runner: SimpleRunner = _default_simple_runner,
    provider_runner: Any = subprocess.run,
    reply_runner: CommandRunner = _default_simple_runner,
    proposal_deliverer: ProposalDeliverer | None = None,
    private_admitter: Callable[[str, dict[str, object]], Mapping[str, object]] | None = None,
    mention_capture: Mapping[str, object] | None = None,
    live_turn_capture: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Consume one bounded event batch for an App and reuse Inbox reply/ACK."""

    profile_configs = _active_profile_configs(snapshot)
    profile_config = profile_configs.get(profile)
    if profile_config is None:
        return {"ok": True, "status": "inactive", "event_count": 0, "replied_count": 0}
    cli_bin = str(profile_config["cli_bin"])
    result = consume_runner(
        [
            cli_bin,
            "--profile",
            profile,
            "event",
            "consume",
            "im.message.receive_v1",
            "--as",
            "bot",
            "--timeout",
            "5s",
            "--max-events",
            "50",
            "--jq",
            _EVENT_PROJECTION,
            "--quiet",
        ]
    )
    if int(result.get("returncode") or 0) != 0:
        return {
            "ok": False,
            "status": "consume_failed",
            "event_count": 0,
            "replied_count": 0,
        }

    events = _event_payloads(result.get("stdout"))
    replied_count = 0
    event_statuses: list[str] = []
    event_reasons: list[str | None] = []
    for event in events:
        if _native_profile_event(snapshot, profile, event):
            private_result = private_admitter(profile, dict(event)) if private_admitter else {"status": "private_admission_unavailable"}
            event_statuses.append(str(private_result.get("status") or "unknown"))
            event_reasons.append(None)
            continue
        scoped, rejection = _profile_event_route(
            profile=profile,
            snapshot=snapshot,
            event=event,
        )
        if scoped is None:
            event_statuses.append(rejection)
            event_reasons.append(None)
            continue
        target_payload, binding_payloads, routed_event = scoped
        try:
            event_result = process_lark_goal_topic_event(
                target_payload=target_payload,
                binding_payloads=binding_payloads,
                event=routed_event,
                runtime_root=runtime_root,
                goal_contexts=(
                    snapshot.get("goal_contexts")
                    if isinstance(snapshot.get("goal_contexts"), Mapping)
                    else {}
                ),
                answer=answer,
                reply_runner=reply_runner,
                provider_runner=provider_runner,
                proposal_deliverer=proposal_deliverer,
                mention_capture=mention_capture,
                live_turn_capture=live_turn_capture,
            )
        except Exception:
            event_statuses.append("processing_failed")
            event_reasons.append(None)
            continue
        event_statuses.append(str(event_result.get("status") or "unknown"))
        event_reasons.append(str(event_result.get("reason") or "") or None)
        replied_count += int(event_result.get("status") == "replied_and_acknowledged")
    return {
        "ok": True,
        "status": "polled",
        "event_count": len(events),
        "replied_count": replied_count,
        "event_statuses": event_statuses,
        "event_reasons": event_reasons,
    }


def stream_lark_goal_topic_profile(
    *,
    profile: str,
    snapshot_provider: SnapshotProvider,
    stop: threading.Event,
    runtime_root: str | Path,
    answer: Answer,
    process_factory: ProcessFactory = _default_process_factory,
    provider_runner: Any = subprocess.run,
    reply_runner: CommandRunner = _default_simple_runner,
    health_sink: HealthSink | None = None,
    proposal_deliverer: ProposalDeliverer | None = None,
    private_admitter: Callable[[str, dict[str, object]], Mapping[str, object]] | None = None,
    capture_mention: MentionCapture | None = None,
    review_callback_handler: ReviewCallbackHandler | None = None,
    callback_availability_sink: Callable[[Callable[[], bool]], None] | None = None,
) -> dict[str, Any]:
    """Keep one bounded long-lived CLI consumer attached between messages."""

    snapshot = snapshot_provider()
    profile_config = _active_profile_configs(snapshot).get(profile)
    if profile_config is None:
        return {
            "ok": True,
            "status": "inactive",
            "event_count": 0,
            "replied_count": 0,
        }
    cli_bin = str(profile_config["cli_bin"])
    process = process_factory(
        [
            cli_bin,
            "--profile",
            profile,
            "event",
            "consume",
            "im.message.receive_v1",
            "--as",
            "bot",
            "--timeout",
            "30m",
            "--max-events",
            "0",
            "--jq",
            _EVENT_PROJECTION,
        ]
    )
    callback_disconnected = threading.Event()
    callback_stream = (
        start_team_plan_review_callback_stream(
            snapshot=snapshot,
            profile=profile,
            cli_bin=cli_bin,
            process_factory=process_factory,
            parent_stop=stop,
            handler=review_callback_handler,
            availability_sink=callback_availability_sink,
        )
        if review_callback_handler is not None
        else None
    )
    if health_sink is not None:
        # A live child process is not proof that lark-cli registered a consumer
        # with its local event bus.  Keep the connection non-ready until the
        # provider emits its explicit ready marker (or a real event arrives).
        health_sink({"status": "starting", "error_code": None})
    watcher_done = threading.Event()
    configuration_removed = threading.Event()

    def stop_consumer() -> None:
        while not watcher_done.wait(1.0):
            if stop.is_set():
                if process.poll() is None:
                    process.terminate()
                if callback_stream is not None:
                    callback_stream.terminate()
                return
            if dispatch.failed:
                # A worker can fail while stdout waits indefinitely for the
                # next event. Unblock that reader so close drains active work
                # and raises into the existing service retry owner. Do not set
                # stop: failure must not become a requested, non-retrying exit.
                try:
                    with result_lock:
                        if health_sink is not None:
                            health_sink(
                                {"status": "failed", "error_code": "lark_event_listener_failed"}
                            )
                finally:
                    if process.poll() is None:
                        process.terminate()
                    if callback_stream is not None:
                        callback_stream.terminate()
                return
            if callback_stream is not None and callback_stream.disconnected():
                callback_disconnected.set()
                if process.poll() is None:
                    process.terminate()
                return
            try:
                configured = profile in _active_profile_configs(snapshot_provider())
            except Exception:
                # A transient source read must not tear down a healthy route.
                continue
            if not configured:
                configuration_removed.set()
                stop.set()
                if process.poll() is None:
                    process.terminate()
                if callback_stream is not None:
                    callback_stream.terminate()
                return

    watcher = threading.Thread(
        target=stop_consumer,
        name=f"loopx-lark-stop-{profile}",
        daemon=True,
    )
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

    def report_result(result: Mapping[str, Any]) -> None:
        nonlocal event_count, replied_count

        events = int(result.get("event_count") or 0)
        if not events:
            return
        replies = int(result.get("replied_count") or 0)
        statuses = list(result.get("event_statuses") or [])
        reasons = list(result.get("event_reasons") or [])
        with result_lock:
            event_count += events
            replied_count += replies
            if health_sink is not None:
                failed = dispatch.failed
                health_sink({
                    "status": "failed" if failed else "listening" if provider_ready else "starting",
                    "error_code": "lark_event_listener_failed" if failed else None,
                    "event_count": events,
                    "replied_count": replies,
                    "last_event_status": str(statuses[-1]) if statuses else None,
                    "last_event_reason": str(reasons[-1]) if reasons and reasons[-1] else None,
                })
            print(json.dumps({
                "schema_version": "loopx_lark_goal_topic_runtime_event_v0",
                "event_count": events, "replied_count": replies,
                "event_statuses": statuses, "event_reasons": reasons,
            }, separators=(",", ":")), flush=True)

    def dispatch_event(
        event: Mapping[str, object], mention: Mapping[str, object] | None,
        current_snapshot: Mapping[str, object],
        *, live_turn_capture: Mapping[str, object] | None = None,
    ) -> None:
        message_id = str(event.get("message_id") or "")
        record_event_timing(profile, message_id, "dispatch_started")
        record_event_timing(profile, message_id, "dispatch_snapshot_ready")
        result = poll_lark_goal_topic_profile_once(
            profile=profile,
            snapshot=current_snapshot,
            runtime_root=runtime_root,
            answer=answer,
            consume_runner=lambda _args: {
                "returncode": 0,
                "stdout": json.dumps(event, ensure_ascii=False),
                "stderr": "",
            },
            provider_runner=provider_runner,
            reply_runner=reply_runner,
            proposal_deliverer=proposal_deliverer,
            private_admitter=private_admitter,
            mention_capture=mention,
            live_turn_capture=live_turn_capture,
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
    provider_ref = _opaque_digest(profile_config.get("bot_app_id") or profile)
    intake = ProfileTopicIntake(root, provider_ref)

    def report_retained_input() -> None:
        report_result({
            "event_count": 1, "replied_count": 0,
            "event_statuses": ["already_captured"], "event_reasons": [None],
        })

    def matches_input(inbox_key: str, packet: LarkTopicInput, snapshot: Mapping[str, object]) -> bool:
        lane, _, route = _profile_event_lane(
            profile=profile, snapshot=snapshot, event=packet["event"],
            runtime_root=root, include_capture=True, prepare_intake=True,
        )
        if route is None:
            return False
        ingress_mode = route.route.get("ingress_mode", IngressMode.DIRECT_SESSION.value)
        # Previously captured compatibility input still belongs to its Topic
        # even if another connection no longer reserves a steering worker.
        return (
            (can_steer(snapshot) or ingress_mode == IngressMode.DIRECT_SESSION.value)
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
        intake.restore(
            dispatch,
            lambda key, packet: matches_input(key, packet, snapshot),
        )
        stdout = process.stdout
        if stdout is None:
            return {
                "ok": False,
                "status": "stream_failed",
                "event_count": 0,
                "replied_count": 0,
            }
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
                message_id = str(event.get("message_id") or "")
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
                    profile=profile,
                    snapshot=current_snapshot,
                    event=event,
                    runtime_root=runtime_root,
                    capture_mention=capture_mention if reserve_priority else None,
                    include_capture=True,
                    prepare_intake=reserve_priority,
                )
                record_event_timing(profile, message_id, "lane_ready")
                priority = bool((mention_capture or {}).get("target_turn_id"))
                if reserve_priority and intake_route is not None and not priority:
                    live_turn_capture = None
                    if (intake_route.route.get("ingress_mode") == "live_steering"
                            and mention_capture is None and capture_mention is not None):
                        # A delayed ordinary live input keeps its original Turn.
                        live_turn_capture = capture_mention(intake_route.route)
                    if intake.capture(
                        dispatch, intake_route, lane, event, mention_capture,
                        lambda: _inbox_config(
                            runtime_root=root, route=intake_route.route,
                            target_payload=intake_route.targets,
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
        provider_ready
        and not stopped
        and (
            callback_disconnected.is_set()
            or returncode != 0
            or exit_reason not in {"limit", "timeout"}
        )
    )
    return {
        "ok": configuration_removed.is_set()
        or stopped
        or (returncode == 0 and provider_ready and not unexpected_exit),
        **(
            {
                "error_code": (
                    "lark_review_callback_source_disconnected"
                    if callback_disconnected.is_set()
                    else "lark_event_source_disconnected"
                )
            }
            if unexpected_exit
            else {}
        ),
        "status": (
            "configuration_removed"
            if configuration_removed.is_set()
            else "stopped"
            if stopped
            else "source_disconnected"
            if unexpected_exit
            else "stream_ended"
            if provider_ready
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
    route: Mapping[str, Any],
    text: str,
    work_dir: str | Path,
    objective: str,
    runtime_controller: Any,
) -> str | Mapping[str, Any]:
    """Deliver one Topic message using its exact Agent ingress contract."""
    goal_id = str(route.get("goal_id") or "")
    ingress_mode = str(route.get("ingress_mode") or "direct_session")
    session_id = str(route.get("session_id") or "")
    manager = route.get("conversation_kind") == "manager"
    agent_id = manager_turn_executor(runtime_controller) if manager else str(
        route.get("agent_id") or "codex"
    )
    expected_channel = (
        str(route.get("manager_channel_id") or "") if manager else f"goal.{goal_id}"
    )
    if manager:
        objective = MANAGER_AGENT_OBJECTIVE
    resolved_work_dir = Path(work_dir).expanduser().resolve()
    reply_context = (
        str(conversation_reply_context(dict(route))["context_text"])
        if route.get("parent_id") or route.get("thread_context")
        else ""
    )
    if manager:
        message = manager_message(
            text, route.get("context_materials"), reply_context=reply_context
        )
    else:
        message = (
            "这是来自已绑定 Lark Goal Topic 的用户消息。请直接回答当前问题；"
            "任何 Goal、Todo 或其他持久状态修改只生成预览，等待用户在 LoopX 明确确认后应用。"
            + ("\n\n" + reply_context if reply_context else "")
            + "\n\n用户消息："
            + str(text or "").strip()
        )
    client_turn_id = "lark." + _opaque_digest(
        route.get("message_id"),
        route.get("topic_root_message_id"),
    )
    native_mention = route.get("native_mention") is True
    if native_mention:
        from ...capabilities.native_chat.queued_admission import deliver_native_mention

        capture = route.get("mention_capture")
        capture = capture if isinstance(capture, Mapping) else capture_topic_mention(route, runtime_controller)
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
                runtime_controller.registry_path, runtime_controller.manager_scope_resolver, current,
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
                expected_channel=expected_channel,
                agent_id=agent_id,
            ):
                raise LarkGoalTopicTurnFailed(
                    "manager_channel_executor_rebind_required",
                    _session_turn_effect(route),
                )
            raise RuntimeError("bound Agent session is unavailable or no longer matches")
        if ingress_mode == "live_steering":
            capture = route.get("live_turn_capture")
            if isinstance(capture, Mapping):
                if (capture.get("binding_matches") is not True
                        or capture.get("session_id") != session_id
                        or capture.get("connection_id") != route.get("connection_id")):
                    return _steering_reply(route, {"action": "reject", "error_code": "live_steering_session_not_attached"})
                target_turn_id = capture.get("target_turn_id")
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
            if (
                manager
                and route.get("source_sender_id")
                and hasattr(runtime_controller.store, "root")
            ):
                from ...capabilities.manager_context import register_ingress

                register_ingress(
                    runtime_controller.store.root.parent,
                    session_id=session_id,
                    client_turn_id=client_turn_id,
                    channel=expected_channel,
                    sender_id=str(route["source_sender_id"]),
                    message=message,
                    source_id="lark:" + str(route["message_id"]),
                    source_message=(
                        (reply_context + "\n\nUser message:\n" if reply_context else "")
                        + str(text or "").strip()
                    ),
                )
            turn, _created = runtime_controller.enqueue_turn(
                session_id=session_id,
                client_turn_id=client_turn_id,
                message=message,
                work_dir=resolved_work_dir,
                objective=str(objective or goal_id),
                origin="lark",
            )
            if native_mention:
                runtime_controller.store.update_ingress_receipt(
                    session_id, client_turn_id, status="delivered",
                )
    else:
        # Compatibility path for bindings created before the three ingress modes.
        channel_id = "lark." + _opaque_digest(
            route.get("app_ref"),
            route.get("target_ref"),
            route.get("topic_root_message_id"),
        )
        session, _resumed = runtime_controller.open_session(
            goal_id=goal_id,
            agent_id=agent_id,
            work_dir=resolved_work_dir,
            objective=str(objective or goal_id),
            mode="resume_latest",
            channel_id=channel_id,
            agent_goal_id=goal_id,
        )
        session_id = str(session["session_id"])
        turn, _created = runtime_controller.submit_turn(
            session_id=session_id,
            client_turn_id=client_turn_id,
            message=message,
            work_dir=resolved_work_dir,
            objective=str(objective or goal_id),
        )
    completed = runtime_controller.wait_for_turn(
        session_id=session_id,
        turn_id=str(turn["turn_id"]),
    )
    if completed.get("status") != "completed":
        if completed.get("status") not in {"failed", "timed_out", "interrupted"}:
            raise RuntimeError("Lark Goal Topic turn has no terminal receipt")
        raise LarkGoalTopicTurnFailed(
            str(completed.get("error_code") or ""),
            _session_turn_effect(route),
        )
    response = completed.get("response")
    reply_text = (
        str(response.get("message") or "") if isinstance(response, Mapping) else ""
    )
    if not reply_text.strip():
        raise RuntimeError("Lark Goal Topic turn returned no message")
    if not manager:
        return reply_text
    proposal_ids = proposal_ids_after_turn(
        runtime_controller,
        session_id=session_id,
        turn_id=str(turn["turn_id"]),
    )
    if proposal_ids:
        return {
            "response_text": reply_text,
            "proposal_ids": proposal_ids,
        }
    return reply_text


def _inbox_config(
    *,
    runtime_root: Path,
    route: Mapping[str, Any],
    target_payload: Mapping[str, Any],
) -> tuple[Path, str]:
    target_ref = str(route.get("target_ref") or "")
    target = goal_channel_target_for_name(target_payload, target_ref)
    if target is None:
        raise ValueError("the routed Lark target is unavailable")
    channel = (
        target.get("channel") if isinstance(target.get("channel"), Mapping) else {}
    )
    identity = (
        target.get("identity") if isinstance(target.get("identity"), Mapping) else {}
    )
    chat_id = str(channel.get("chat_id") or "")
    profile = str(route.get("app_ref") or "")
    bot_display_name = str(identity.get("bot_display_name") or profile)
    digest = topic_inbox_key(profile, chat_id, str(route.get("topic_root_message_id") or ""))
    config_ref = f".loopx/config/lark-goal-topics/{digest}.json"
    config_path = runtime_root / config_ref
    payload = {
        "schema_version": "lark_event_inbox_config_v0",
        "enabled": True,
        "inbox_dir": f".loopx/inbox/lark-goal-topics/{digest}",
        "capture_scope": "configured_chat_all",
        "topic_root_message_id": str(route.get("topic_root_message_id") or ""),
        "material_review": {
            "enabled": route.get("conversation_kind") == "manager",
            "drain_limit": MANAGER_CONTEXT_ITEM_LIMIT,
        },
        "reply": {
            "enabled": True,
            "sender_profile": profile,
            "sender_identity": "bot",
            "bot_display_name": bot_display_name,
            "bot_app_id": str(identity.get("bot_app_id") or ""),
            "bot_open_id": str(identity.get("bot_open_id") or ""),
            "chat_id": chat_id,
            "placement_policy": "source_context",
            "editorial_style": "bullet_points_preferred",
            "received_reaction_policy": (
                "retain" if route.get("conversation_kind") == "manager" else "transient"
            ),
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private_json_atomic(config_path, payload)
    return config_path, config_ref


def process_lark_goal_topic_event(
    *,
    target_payload: Mapping[str, Any],
    binding_payloads: Mapping[str, Mapping[str, Any]],
    event: Mapping[str, Any],
    runtime_root: str | Path,
    goal_contexts: Mapping[str, Mapping[str, Any]] | None = None,
    answer: Answer,
    reply_runner: CommandRunner,
    provider_runner: Any | None = None,
    proposal_deliverer: ProposalDeliverer | None = None,
    mention_capture: Mapping[str, object] | None = None,
    live_turn_capture: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Single-flight a manager or live mention through ingress, reply and ACK.

    The inbox and delivery receipt make sequential retries safe, but two
    listeners can otherwise both observe an absent receipt and generate two
    different answers before either writes it. The lock is per source message
    and spans the entire effect, including provider readback and inbox ACK.
    """

    decision = decide_lark_topic_event(
        target_payload=target_payload,
        binding_payloads=binding_payloads,
        event=event,
        runtime_root=runtime_root,
    )
    route = decision.get("route")
    native_mention = isinstance(route, Mapping) and _route_native_mention(event, route, target_payload)
    if mention_capture is not None and not native_mention:
        return {"ok": True, "status": "ignored", "reason": "mention_binding_changed"}
    if live_turn_capture is not None:
        if not isinstance(route, Mapping) or route.get("ingress_mode") != "live_steering" or native_mention:
            return {"ok": True, "status": "ignored", "reason": LarkTopicEventDecisionReason.BINDING_UNAVAILABLE.value}
        route = {**route, "live_turn_capture": live_turn_capture}
        decision = {**decision, "route": route}
    if native_mention:
        route = {
            **route,
            "native_mention": True,
            "native_mention_text_only": event.get("message_type") == "text" and not event.get("attachment_count"),
            "mention_capture": mention_capture,
        }
        decision = {**decision, "route": route}
    if isinstance(route, Mapping) and (
        native_mention or (route.get("conversation_kind") == "manager"
                           and route.get("authority_mode") == ManagerAuthorityMode.TURN_AUTHORIZED.value)
    ):
        message_id = str(route.get("message_id") or "")
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            raise ValueError("manager route has an invalid message id")
        lock_dir = Path(runtime_root).expanduser().resolve() / ".loopx/locks/lark-manager"
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(lock_dir, 0o700)
        with exclusive_file_lock(
            lock_dir / message_id,
            timeout_seconds=960,
            operation="lark_manager_message",
        ):
            return _process_lark_goal_topic_event(
                target_payload=target_payload,
                event=event,
                runtime_root=runtime_root,
                goal_contexts=goal_contexts,
                answer=answer,
                reply_runner=reply_runner,
                provider_runner=provider_runner,
                proposal_deliverer=proposal_deliverer,
                decision=decision,
            )
    return _process_lark_goal_topic_event(
        target_payload=target_payload,
        event=event,
        runtime_root=runtime_root,
        goal_contexts=goal_contexts,
        answer=answer,
        reply_runner=reply_runner,
        provider_runner=provider_runner,
        proposal_deliverer=proposal_deliverer,
        decision=decision,
    )


def _process_lark_goal_topic_event(
    *,
    target_payload: Mapping[str, Any],
    event: Mapping[str, Any],
    runtime_root: str | Path,
    goal_contexts: Mapping[str, Mapping[str, Any]] | None,
    answer: Answer,
    reply_runner: CommandRunner,
    provider_runner: object | None,
    proposal_deliverer: ProposalDeliverer | None,
    decision: Mapping[str, Any],
) -> dict[str, object]:
    """Persist, answer, reply, and ACK the already-routed Topic event."""

    route = decision.get("route")
    if route is None:
        return {
            "ok": True,
            "status": "ignored",
            "reason": str(
                decision.get("reason")
                or LarkTopicEventDecisionReason.BINDING_UNAVAILABLE.value
            ),
        }
    ingress_mode = str(route.get("ingress_mode") or "direct_session")
    if ingress_mode == "async_inbox":
        contexts = goal_contexts if isinstance(goal_contexts, Mapping) else {}
        context = contexts.get(str(route.get("goal_id") or ""))
        context = context if isinstance(context, Mapping) else {}
        work_dir = str(context.get("work_dir") or "").strip()
        config_ref = str(route.get("inbox_config_ref") or "").strip()
        if not work_dir or not config_ref:
            return {
                "ok": False,
                "status": "agent_inbox_unavailable",
                "goal_id": route["goal_id"],
            }
        root = Path(work_dir).expanduser().resolve()
        config_path = Path(config_ref)
    else:
        root = Path(runtime_root).expanduser().resolve()
        config_path, config_ref = _inbox_config(
            runtime_root=root,
            route=route,
            target_payload=target_payload,
        )
    canonical: dict[str, Any] = {
        "schema_version": "lark_event_inbox_event_v0",
        "event_id": str(event.get("event_id") or event.get("message_id") or ""),
        "message_id": str(event.get("message_id") or ""),
        "create_time": str(event.get("create_time") or ""),
        "content": str(event.get("content") or ""),
        "sender_type": str(event.get("sender_type") or ""),
        "sender_id": str(event.get("sender_id") or ""),
        "root_id": str(event.get("root_id") or ""),
        "thread_id": str(event.get("thread_id") or ""),
        "thread_context": event.get("thread_context"),
        "parent_id": str(event.get("parent_id") or ""),
        "mentions": event.get("mentions")
        if isinstance(event.get("mentions"), list)
        else [],
        "reply_context_verified": event.get("reply_context_verified") is True,
        "reply_to_bot": event.get("reply_to_bot") is True,
        "reply_context": event.get("reply_context"),
    }
    ingest = ingest_lark_event_inbox(
        project=root,
        config_path=config_path,
        events=[canonical],
        execute=True,
    )
    manager = route.get("conversation_kind") == "manager"
    authority_mode = (
        parse_manager_authority_mode(route.get("authority_mode")) if manager else None
    )
    if manager and authority_mode is None:
        return invalid_manager_authority_result(route, inbox_config_ref=config_ref)
    retention = {"discarded_count": 0, "expired_count": 0, "overflow_count": 0}
    if manager:
        _, retention = manager_context_projection(
            project=root,
            config_path=config_path,
            current_message_id=str(canonical.get("message_id") or ""),
        )
    if authority_mode is ManagerAuthorityMode.CONTEXT_ONLY:
        return {
            "ok": True,
            "status": (
                "context_only_captured"
                if int(ingest.get("accepted_count") or 0)
                else "context_only_already_captured"
            ),
            "reason": (
                normalize_lark_topic_event_rejection_reason(route.get("trigger_reason"))
                or LarkTopicEventDecisionReason.NOT_ADDRESSED.value
            ),
            "goal_id": route["goal_id"],
            "inbox_config_ref": config_ref,
            "turn_authorized": False,
            "model_invoked": False,
            "external_write_performed": False,
            "context_retention_discarded_count": retention["discarded_count"],
        }
    context_sync: Mapping[str, Any] | None = None
    if route.get("conversation_kind") == "manager" and provider_runner is not None:
        context_sync = sync_manager_context(
            project=root,
            config_path=config_path,
            target_payload=target_payload,
            target_ref=str(route.get("target_ref") or ""),
            provider_runner=provider_runner,
        )
    message_id = str(route["message_id"])
    projection = inspect_lark_event_inbox(
        project=root, config_path=config_path, limit=0
    )
    if manager:
        projection, retention = manager_context_projection(
            project=root,
            config_path=config_path,
            current_message_id=message_id,
        )
    pending_ids = {
        str(item.get("message_id") or "")
        for item in projection.get("items", [])
        if isinstance(item, Mapping)
    }
    if message_id not in pending_ids:
        return {
            "ok": True,
            "status": "already_acknowledged",
            "goal_id": route["goal_id"],
            "inbox_config_ref": config_ref,
        }
    if ingress_mode == "async_inbox":
        return {
            "ok": True,
            "status": "queued_for_agent",
            "goal_id": route["goal_id"],
            "agent_id": route.get("agent_id"),
            "inbox_config_ref": config_ref,
            **({"reason": "async_inbox_requires_bound_session"} if route.get("native_mention") else {}),
        }
    # Receipt ACK is visible while the synchronous manager is reasoning. The
    # private reaction ledger makes retries idempotent. Manager received ACKs
    # remain visible; final reply only clears transient processing indicators.
    # A cosmetic reaction failure must not suppress the actual answer.
    received_reaction = None
    if manager:
        profile = str(route.get("app_ref") or "")
        try:
            received_reaction = ensure_lark_event_inbox_received_reaction(
                project=root,
                config_path=config_path,
                event=canonical,
                create_reaction=lambda mid, emoji: _create_reaction(
                    runner=reply_runner,
                    profile=profile,
                    message_id=mid,
                    emoji_type=emoji,
                ),
                delete_reaction=lambda mid, rid: _delete_reaction(
                    runner=reply_runner,
                    profile=profile,
                    message_id=mid,
                    reaction_id=rid,
                ),
            )
        except (OSError, ValueError):
            received_reaction = {"ok": False, "status": "reaction_state_unavailable"}
        if not received_reaction.get("ok"):
            logging.getLogger(__name__).warning(
                "Lark manager received reaction was not verified"
            )
    # Sender provenance comes from the provider event, never the model response.
    # Context-only messages stay visibly non-authoritative inside the next
    # addressed manager Turn and never call the model on their own.
    context_materials = (
        manager_context_materials(projection, current_message_id=message_id)
        if manager
        else []
    )
    captured_event = next(
        item for item in projection["items"] if item.get("message_id") == message_id
    )
    if route.get("native_mention") and (
        not captured_event.get("target_mention_count") or captured_event.get("historical_context_only")
        or captured_event.get("sender_id") != canonical["sender_id"]
    ):
        return {"ok": False, "status": "native_mention_proof_changed", "goal_id": route["goal_id"]}
    route = {
        **route,
        "source_sender_id": str(canonical.get("sender_id") or ""),
        "source_conversation_id": str(event.get("chat_id") or ""),
        # The immutable captured event owns reply ancestry on delivery retries.
        "parent_id": captured_event.get("parent_id", ""),
        "reply_context": captured_event.get("reply_context"),
        "root_id": captured_event.get("root_id", ""),
        "thread_id": captured_event.get("thread_id", ""),
        "thread_context": captured_event.get("thread_context"),
        **({"native_mention_fresh": int(ingest.get("accepted_count") or 0) > 0} if route.get("native_mention") else {}),
        **({"context_materials": context_materials} if context_materials else {}),
    }
    delivery_path: Path | None = None
    delivery_state: dict[str, Any] | None = None
    saved_response_reused = False
    if manager:
        try:
            delivery_path, delivery_state = _load_manager_delivery(
                project=root, config_path=config_path, event=canonical
            )
        except (OSError, ValueError):
            return {
                "ok": False,
                "status": "reply_delivery_state_invalid",
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "source_acknowledged": False,
            }
        if delivery_state is not None and delivery_state["status"] == "acknowledged":
            return {
                "ok": False,
                "status": "reply_delivery_state_invalid",
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "source_acknowledged": False,
            }
        try:
            route = restore_manager_context_route(
                route,
                projection,
                current_message_id=message_id,
                delivery_state=delivery_state,
            )
        except ValueError:
            return unavailable_manager_context_result(
                route, inbox_config_ref=config_ref
            )
        if "context_materials" in route:
            context_materials = route["context_materials"]

    failure_code: str | None = None
    effect_receipt: Mapping[str, Any] | None = None
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
        proposal_ids = [str(value) for value in delivery_state.get("proposal_ids") or []]
    else:
        try:
            answer_result = answer(route, str(canonical["content"]))
            answer_completed = True
        except LarkGoalTopicTurnFailed as exc:
            if not manager:
                raise
            failure_code, failure_text = _manager_failure_reply(exc)
            answer_result = {
                "response_text": failure_text,
                "effect_receipt": exc.effect_receipt,
            }
        except Exception as exc:
            # A synchronous manager route must leave a user-visible, bounded
            # receipt even when the worker raises an untyped exception.  The
            # exception itself may contain private provider details, so only its
            # type is logged and the public reply uses the generic failure label.
            if not manager:
                raise
            logging.getLogger(__name__).warning(
                "Lark manager answer failed with %s", type(exc).__name__
            )
            failure_code, failure_text = _manager_failure_reply(exc)
            answer_result = {
                "response_text": failure_text,
                "effect_receipt": _session_turn_effect(route),
            }
        if isinstance(answer_result, Mapping):
            reply_text = str(answer_result.get("response_text") or "").strip()
            candidate_receipt = answer_result.get("effect_receipt")
            effect_receipt = (
                candidate_receipt if isinstance(candidate_receipt, Mapping) else None
            )
            proposal_ids = [
                str(value)
                for value in answer_result.get("proposal_ids") or []
                if str(value)
            ]
        else:
            reply_text = str(answer_result or "").strip()
        content_format = "markdown" if manager else "text"

    connector = route.get("connector")
    connector = connector if isinstance(connector, Mapping) else None
    if not reply_text:
        answer_completed = False
        if manager:
            # Empty model output is a terminal, non-replayable failure for a
            # manager request.  Reply through the same inbox path so the
            # source is ACKed only after provider verification.
            failure_code = "answer_empty"
            reply_text = (
                "已收到你的消息，但本次没有生成可发送的完整答复。"
                "请求不会自动重放；请在 LoopX 管家会话查看状态或重新发起。"
            )
            effect_receipt = effect_receipt or _session_turn_effect(route)
        else:
            return {
                "ok": False,
                "status": "answer_empty",
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
            }
    rich_text_repairs: list[dict[str, Any]] = []
    if manager:
        # The reader must not receive a one-line answer whose bullet list is
        # still escaped, nor raw braces left by an unresolved template.  Repair
        # those defects before the first send: the strict validator keeps
        # owning unsafe markup, and a repaired markdown answer stays markdown
        # instead of degrading to plain text.
        reply_text, rich_text_repairs = repair_manager_reply_text(reply_text)
        reply_text = present_lark_conversation_text(reply_text)
    if manager and delivery_state is None:
        assert delivery_path is not None
        delivery_state = _pending_manager_delivery(
            event=canonical,
            text=reply_text,
            content_format=content_format,
            effect_receipt=effect_receipt,
            failure_code=failure_code,
            context_material_ids=[
                item["message_id"] for item in context_materials
            ],
            proposal_ids=proposal_ids,
        )
        if rich_text_repairs:
            delivery_state["rich_text_repairs"] = rich_text_repairs
        try:
            _write_manager_delivery(delivery_path, delivery_state)
        except OSError:
            return {
                "ok": False,
                "status": "reply_delivery_state_unavailable",
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "source_acknowledged": False,
            }
    if connector is not None:
        effect_decision = decide_external_event_ack(
            event_id=canonical["event_id"],
            effect_receipt=effect_receipt,
            response_policy=ExternalResponsePolicy.NO_RESPONSE.value,
        )
        if not effect_decision["effect_ready"]:
            return {
                "ok": False,
                "status": "durable_effect_required",
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "ack_decision": effect_decision,
            }

    if delivery_state is not None and delivery_state["status"] == "sent_verified":
        reply = {
            "ok": True,
            "status": "sent_verified",
            "idempotency_key": delivery_state.get("reply_idempotency_key"),
            "external_write_performed": delivery_state.get("external_write_performed")
            is True,
            "verification_performed": True,
            "reply_verified": True,
        }
    else:
        if delivery_state is not None:
            delivery_state["attempt_count"] = (
                int(delivery_state.get("attempt_count") or 0) + 1
            )
            delivery_state["updated_at"] = datetime.now(timezone.utc).isoformat()
            assert delivery_path is not None
            _write_manager_delivery(delivery_path, delivery_state)
        try:
            reply = reply_lark_event_inbox(
                project=root,
                config_path=config_path,
                message_id=message_id,
                text=reply_text,
                content_format=content_format,
                execute=True,
                runner=reply_runner,
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
            delivery_state.update(last_delivery_status="format_unrepresentable",
                updated_at=datetime.now(timezone.utc).isoformat())
            _write_manager_delivery(delivery_path, delivery_state)
            return {"ok": False, "status": "reply_delivery_pending",
                "reason": "reply_format_invalid", "format_degraded": False,
                "goal_id": route["goal_id"], "inbox_config_ref": config_ref,
                "source_acknowledged": False}
    if manager and reply.get("content_format") in {"markdown", "text"}:
        content_format = str(reply["content_format"])
    if manager and delivery_state is not None:
        delivery_state.update(manager_part_delivery_readback(reply))
    if not reply.get("ok"):
        if manager:
            assert delivery_state is not None and delivery_path is not None
            delivery_state.update(
                last_delivery_status=str(reply.get("status") or "reply_failed"),
                last_blocker=str(reply.get("blocker") or "reply_delivery_unverified"),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            _write_manager_delivery(delivery_path, delivery_state)
        return {
            "ok": False,
            "status": (
                "reply_delivery_pending"
                if manager
                else str(reply.get("status") or "reply_failed")
            ),
            **(
                {
                    "delivery_status": str(reply.get("status") or "reply_failed"),
                    "format_degraded": bool(
                        delivery_state and delivery_state.get("format_degraded")
                    ),
                    "rich_text_repairs": list(
                        (delivery_state or {}).get("rich_text_repairs") or []
                    ),
                    **manager_part_delivery_readback(reply),
                    "delivery_notice_sent": reply.get("delivery_notice_sent") is True,
                }
                if manager
                else {}
            ),
            "goal_id": route["goal_id"],
            "blocker": reply.get("blocker"),
            "inbox_config_ref": config_ref,
            **({"source_acknowledged": False} if manager else {}),
        }
    if manager:
        assert delivery_state is not None and delivery_path is not None
        delivery_state.update(
            status="sent_verified",
            delivery_text=reply_text,
            delivery_digest=_manager_delivery_text_digest(reply_text),
            content_format=content_format,
            reply_idempotency_key=reply.get("idempotency_key"),
            external_write_performed=(reply.get("external_write_performed") is True),
            verification_performed=(reply.get("verification_performed") is True),
            reply_verified=(reply.get("reply_verified") is True),
            last_delivery_status=str(reply.get("status") or "sent_verified"),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            _write_manager_delivery(delivery_path, delivery_state)
        except OSError:
            return {
                "ok": False,
                "status": "reply_delivery_receipt_unavailable",
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "source_acknowledged": False,
            }
        proposal_delivery_status = settle_team_plan_proposal_delivery(
            delivery_state=delivery_state,
            delivery_path=delivery_path,
            route=route,
            proposal_ids=proposal_ids,
            proposal_deliverer=proposal_deliverer,
        )
        if proposal_delivery_status is not None:
            return {
                "ok": False,
                "status": proposal_delivery_status,
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "source_acknowledged": False,
            }
    if connector is not None:
        ack_decision = decide_external_event_ack(
            event_id=canonical["event_id"],
            effect_receipt=effect_receipt,
            response_policy=str(connector.get("response_policy") or ""),
            response_receipt=build_external_event_response_receipt(
                event_id=canonical["event_id"],
                external_write_performed=(
                    reply.get("external_write_performed") is True
                ),
                verification_performed=(reply.get("verification_performed") is True),
                response_verified=reply.get("reply_verified") is True,
            ),
        )
        if not ack_decision["ack_allowed"]:
            return {
                "ok": False,
                "status": str(ack_decision["reason"]),
                "goal_id": route["goal_id"],
                "inbox_config_ref": config_ref,
                "ack_decision": ack_decision,
            }
    context_settled_count = settle_manager_context(
        project=root,
        config_path=config_path,
        materials=context_materials if answer_completed else [],
    )
    acknowledge_lark_event_inbox(
        project=root,
        config_path=config_path,
        message_ids=[message_id],
        execute=True,
    )
    if manager:
        assert delivery_state is not None and delivery_path is not None
        delivery_state.update(
            status="acknowledged",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        delivery_state.pop("delivery_text", None)
        delivery_state.pop("effect_receipt", None)
        try:
            _write_manager_delivery(delivery_path, delivery_state)
        except OSError:
            logging.getLogger(__name__).warning(
                "Lark manager delivery receipt cleanup could not be persisted"
            )

    return {
        "ok": failure_code is None,
        "status": "processing_failed" if failure_code else "replied_and_acknowledged",
        **(
            {
                "reason": failure_code,
                "failure_reply_verified": True,
                "source_acknowledged": True,
            }
            if failure_code
            else {}
        ),
        "received_reaction_status": (received_reaction or {}).get("status"),
        **(
            {
                "saved_response_reused": saved_response_reused,
                "format_degraded": bool(delivery_state.get("format_degraded")),
                "rich_text_repairs": list(
                    delivery_state.get("rich_text_repairs") or []
                ),
                **manager_part_delivery_readback(delivery_state),
            }
            if manager and delivery_state is not None
            else {}
        ),
        "context_material_count": len(context_materials),
        "context_settled_count": context_settled_count,
        "context_retention_discarded_count": retention["discarded_count"],
        "context_sync_status": (
            str(context_sync.get("status") or "unknown")
            if context_sync is not None
            else "not_attempted"
        ),
        "goal_id": route["goal_id"],
        "inbox_config_ref": config_ref,
    }


# Preserve the established import path while keeping worker lifecycle out of
# the already hot message-processing module.
from .goal_topic_runtime_service import (  # noqa: E402,F401
    LarkGoalTopicRuntimeService,
)
