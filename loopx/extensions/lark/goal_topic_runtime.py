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
from typing import Any

from ...chat_manager import MANAGER_AGENT_OBJECTIVE
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
    ExternalResponsePolicy,
    build_external_event_response_receipt,
    decide_external_event_ack,
)
from .event_inbox import (
    MESSAGE_ID_PATTERN,
    acknowledge_lark_event_inbox,
    ingest_lark_event_inbox,
    inspect_lark_event_inbox,
)
from .goal_channel_contracts import LarkTopicEventDecisionReason, bindings_for_goal
from .goal_channel_targets import goal_channel_target_for_name
from .goal_topic_connections import decide_lark_topic_event
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
    deliver_manager_reply_after_length_failure,
    manager_part_delivery_pending_result,
    manager_part_delivery_readback,
)
from .manager_reply_format import repair_manager_reply_text
from .outbound import LarkOutboundTextError, safe_lark_plain_text_fallback
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

Answer = Callable[[Mapping[str, Any], str], str | Mapping[str, Any]]
SnapshotProvider = Callable[[], Mapping[str, Any]]
SimpleRunner = Callable[[list[str]], Mapping[str, Any]]
ProcessFactory = Callable[[list[str]], Any]
HealthSink = Callable[[Mapping[str, Any]], None]
ProposalDeliverer = Callable[
    [Mapping[str, Any], list[str]], Mapping[str, Any]
]
ReviewCallbackHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class LarkGoalTopicTurnFailed(RuntimeError):
    """A terminal runtime receipt, without copying arbitrary upstream details."""

    def __init__(self, error_code: str, effect_receipt: Mapping[str, Any]) -> None:
        super().__init__("Lark Goal Topic turn did not complete")
        self.error_code = error_code
        self.effect_receipt = effect_receipt


_EVENT_PROJECTION = (
    '{schema_version:"lark_event_inbox_event_v0",'
    "event_id:(.event_id // .message_id // .id),"
    "message_id:(.message_id // .id),"
    "create_time:.create_time,content:.content,"
    "sender_id:(.sender_id // .sender.id // .sender.sender_id "
    "// .event.sender.sender_id // .event.sender.id),"
    "sender_type:(.sender_type // .sender.sender_type // .event.sender.sender_type),"
    "chat_id:.chat_id,"
    "root_id:(.root_id // .message.root_id // .event.message.root_id),"
    "parent_id:(.parent_id // .reply_to // .message.parent_id // .message.reply_to "
    "// .event.message.parent_id // .event.message.reply_to),"
    "thread_id:(.thread_id // .message.thread_id // .event.message.thread_id),"
    "mentions:(.mentions // .message.mentions // .event.message.mentions // [])}"
)

_EVENT_READY_PREFIX = "[event] ready "
_EVENT_DIAGNOSTIC_PREFIX = "[event] "
_EVENT_EXIT_REASON = re.compile(r"\(reason: (limit|timeout|signal)\)$")


def _active_profile_configs(snapshot: Mapping[str, Any]) -> dict[str, dict[str, str]]:
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
    target_payload: Mapping[str, Any], *, profile: str, chat_id: str
) -> tuple[str, Mapping[str, Any]] | None:
    targets = target_payload.get("targets")
    targets = targets if isinstance(targets, Mapping) else {}
    for target_ref, target in targets.items():
        if not isinstance(target, Mapping) or target.get("enabled") is not True:
            continue
        channel = target.get("channel")
        channel = channel if isinstance(channel, Mapping) else {}
        identity = target.get("identity")
        identity = identity if isinstance(identity, Mapping) else {}
        if (
            str(channel.get("chat_id") or "") == chat_id
            and str(identity.get("sender_profile") or "") == profile
        ):
            return str(target_ref), target
    return None


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

    target_payload = snapshot.get("target_payload")
    target_payload = target_payload if isinstance(target_payload, Mapping) else {}
    binding_payloads = snapshot.get("binding_payloads")
    binding_payloads = binding_payloads if isinstance(binding_payloads, Mapping) else {}
    events = _event_payloads(result.get("stdout"))
    replied_count = 0
    event_statuses: list[str] = []
    event_reasons: list[str | None] = []
    for event in events:
        chat_id = str(event.get("chat_id") or "")
        target_match = _target_for_profile_chat(
            target_payload,
            profile=profile,
            chat_id=chat_id,
        )
        if target_match is None:
            event_statuses.append("target_unmatched")
            event_reasons.append(None)
            continue
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
                event_statuses.append("topic_context_ambiguous")
                event_reasons.append(None)
                continue
            if not candidate_roots:
                event_statuses.append("topic_context_missing")
                event_reasons.append(None)
                continue
            routed_event["root_id"] = candidate_roots[0]
        try:
            event_result = process_lark_goal_topic_event(
                target_payload=target_payload,
                binding_payloads=_binding_payloads_for_target(
                    binding_payloads,
                    target_ref=target_ref,
                ),
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
    review_callback_handler: ReviewCallbackHandler | None = None,
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
    watcher.start()
    event_count = 0
    replied_count = 0
    provider_ready = False
    exit_reason: str | None = None
    try:
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
            if stripped.startswith(_EVENT_READY_PREFIX):
                provider_ready = True
                if health_sink is not None:
                    health_sink({"status": "listening", "error_code": None})
                continue
            if stripped.startswith("[event] exited "):
                match = _EVENT_EXIT_REASON.search(stripped)
                exit_reason = match.group(1) if match else None
                continue
            if stripped.startswith(_EVENT_DIAGNOSTIC_PREFIX):
                continue
            result = poll_lark_goal_topic_profile_once(
                profile=profile,
                snapshot=snapshot_provider(),
                runtime_root=runtime_root,
                answer=answer,
                consume_runner=lambda _args, payload=line: {
                    "returncode": 0,
                    "stdout": payload,
                    "stderr": "",
                },
                provider_runner=provider_runner,
                reply_runner=reply_runner,
                proposal_deliverer=proposal_deliverer,
            )
            if int(result.get("event_count") or 0) and not provider_ready:
                # A provider event is stronger readiness evidence than a
                # diagnostic marker and protects compatibility with providers
                # that omit the marker while still emitting the typed stream.
                provider_ready = True
                if health_sink is not None:
                    health_sink({"status": "listening", "error_code": None})
            event_count += int(result.get("event_count") or 0)
            replied_count += int(result.get("replied_count") or 0)
            if health_sink is not None and int(result.get("event_count") or 0):
                statuses = list(result.get("event_statuses") or [])
                reasons = list(result.get("event_reasons") or [])
                health_sink(
                    {
                        "status": "listening",
                        "error_code": None,
                        "event_count": int(result.get("event_count") or 0),
                        "replied_count": int(result.get("replied_count") or 0),
                        "last_event_status": str(statuses[-1]) if statuses else None,
                        "last_event_reason": (
                            str(reasons[-1]) if reasons and reasons[-1] else None
                        ),
                    }
                )
            if int(result.get("event_count") or 0):
                print(
                    json.dumps(
                        {
                            "schema_version": "loopx_lark_goal_topic_runtime_event_v0",
                            "event_count": int(result.get("event_count") or 0),
                            "replied_count": int(result.get("replied_count") or 0),
                            "event_statuses": list(result.get("event_statuses") or []),
                            "event_reasons": list(result.get("event_reasons") or []),
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
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
    if manager:
        message = manager_message(text, route.get("context_materials"))
    else:
        message = (
            "这是来自已绑定 Lark Goal Topic 的用户消息。请直接回答当前问题；"
            "任何 Goal、Todo 或其他持久状态修改只生成预览，等待用户在 LoopX 明确确认后应用。"
            "\n\n用户消息：" + str(text or "").strip()
        )
    client_turn_id = "lark." + _opaque_digest(
        route.get("message_id"),
        route.get("topic_root_message_id"),
    )
    if ingress_mode in {"live_steering", "session_queue"}:
        session = runtime_controller.store.load_session(session_id)
        if (
            session is None
            or (not manager and session.get("goal_id") != goal_id)
            or session.get("agent_id") != agent_id
            or session.get("channel_id") != expected_channel
            or session.get("status") == "closed"
        ):
            if manager and manager_session_requires_executor_rebind(
                session,
                expected_channel=expected_channel,
                agent_id=agent_id,
            ):
                raise LarkGoalTopicTurnFailed(
                    "manager_channel_executor_rebind_required",
                    _session_turn_effect(route),
                )
            raise RuntimeError(
                "bound Agent session is unavailable or no longer matches"
            )
        if ingress_mode == "live_steering":
            turn, _created = runtime_controller.steer_active_turn(
                session_id=session_id,
                client_ingress_id=client_turn_id,
                message=message,
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
                    source_message=str(text or "").strip(),
                )
            turn, _created = runtime_controller.enqueue_turn(
                session_id=session_id,
                client_turn_id=client_turn_id,
                message=message,
                work_dir=resolved_work_dir,
                objective=str(objective or goal_id),
                origin="lark",
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
    digest = hashlib.sha256(
        f"{profile}\0{chat_id}\0{route.get('topic_root_message_id')}".encode("utf-8")
    ).hexdigest()[:20]
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
    temporary = config_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(config_path)
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
) -> dict[str, Any]:
    """Route, persist, answer, reply, and ACK one bound Topic event."""

    decision = decide_lark_topic_event(
        target_payload=target_payload,
        binding_payloads=binding_payloads,
        event=event,
        runtime_root=runtime_root,
    )
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
        "parent_id": str(event.get("parent_id") or ""),
        "mentions": event.get("mentions")
        if isinstance(event.get("mentions"), list)
        else [],
        "reply_context_verified": event.get("reply_context_verified") is True,
        "reply_to_bot": event.get("reply_to_bot") is True,
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
            "reason": "not_addressed",
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
    route = {
        **route,
        "source_sender_id": str(canonical.get("sender_id") or ""),
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
            )
        except LarkOutboundTextError:
            if not manager:
                raise
            # The rich body did not fit. Degrade presentation first, then split
            # the degraded body: a persisted manager answer must be delivered in
            # bounded parts instead of becoming `format_unrepresentable` with
            # nothing on the channel.
            try:
                reply_text = safe_lark_plain_text_fallback(reply_text)
                content_format = "text"
                assert delivery_state is not None and delivery_path is not None
                delivery_state.update(
                    delivery_text=reply_text,
                    delivery_digest=_manager_delivery_text_digest(reply_text),
                    content_format=content_format,
                    format_degraded=True,
                    attempt_count=int(delivery_state.get("attempt_count") or 0) + 1,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
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
                    )
                except LarkOutboundTextError:
                    if not reply_text.strip():
                        raise
                    part_reply, part_failure = (
                        deliver_manager_reply_after_length_failure(
                            reply_text=reply_text,
                            delivery_state=delivery_state,
                            delivery_path=delivery_path,
                            write_delivery=_write_manager_delivery,
                            reply_runner=reply_runner,
                            root=root,
                            config_path=config_path,
                            message_id=message_id,
                        )
                    )
                    if part_failure:
                        return manager_part_delivery_pending_result(
                            reason=part_failure,
                            delivery_state=delivery_state,
                            goal_id=route["goal_id"],
                            inbox_config_ref=config_ref,
                        )
                    reply = part_reply
            except (LarkOutboundTextError, ValueError):
                assert delivery_state is not None and delivery_path is not None
                delivery_state.update(
                    last_delivery_status="format_unrepresentable",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                _write_manager_delivery(delivery_path, delivery_state)
                return {
                    "ok": False,
                    "status": "reply_delivery_pending",
                    "reason": "reply_format_invalid",
                    "format_degraded": bool(delivery_state.get("format_degraded")),
                    "goal_id": route["goal_id"],
                    "inbox_config_ref": config_ref,
                    "source_acknowledged": False,
                }
    if manager and reply.get("content_format") in {"markdown", "text"}:
        content_format = str(reply["content_format"])
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
