"""Lark confirmation surfaces for one canonical ``team.plan`` proposal."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...chat_action_store import ActionConflictError, ChatActionStore
from ...control_plane.runtime.runtime_projection_route import (
    resolve_goal_source_runtime_route,
)
from ...file_lock import exclusive_file_lock
from ...history import load_registry
from .card_callback import (
    callback_card_content_matches as _callback_card_content_matches,
    callback_timestamp,
    operator_membership_verified as _operator_membership_verified,
    patch_result_card as _patch_operation_result_card,
    read_callback_card_content as _read_callback_card_content,
    update_callback_card as _update_callback_card,
)
from .goal_channel_contracts import (
    binding_for_goal,
    bindings_for_goal,
    default_goal_channel_binding_path,
    read_goal_channel_binding,
)
from .goal_channel_delivery_contract import (
    goal_channel_binding_digest,
    goal_channel_delivery_route,
)
from .goal_channel_message_delivery import (
    GoalChannelDeliveryStageError,
    GoalChannelMessageDeliverySession,
)
from .goal_channel_targets import (
    default_goal_channel_target_path,
    goal_channel_target_for_name,
    read_goal_channel_targets,
)
from .manager_reply_delivery import (
    TEAM_PLAN_DELIVERY_RECEIPT_SCHEMA_VERSION,
    validate_team_plan_delivery_receipt,
    write_delivery as write_manager_delivery,
)
from .presentation.kanban import CommandRunner, default_subprocess_runner
from .presentation.team_plan import (
    TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION,
    build_team_plan_result_card,
    build_team_plan_review_card,
)


TEAM_PLAN_CALLBACK_RECEIPT_SCHEMA_VERSION = "lark_team_plan_callback_receipt_v0"
_EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,240}$")
_MESSAGE_ID = re.compile(r"^om_[A-Za-z0-9_-]+$")
_CHAT_ID = re.compile(r"^oc_[A-Za-z0-9_-]+$")
_OPEN_ID = re.compile(r"^ou_[A-Za-z0-9_-]+$")
_EVENT_DIAGNOSTIC_PREFIX = "[event] "

ProcessFactory = Callable[[list[str]], Any]
ReviewCallbackHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class TeamPlanReviewCallbackStream:
    """Own the companion card-callback consumer for one Lark profile."""

    def __init__(
        self,
        *,
        process: Any,
        thread: threading.Thread,
        stop: threading.Event,
    ) -> None:
        self.process = process
        self.thread = thread
        self.stop = stop

    def disconnected(self) -> bool:
        return self.process.poll() is not None

    def terminate(self) -> None:
        self.stop.set()
        if self.process.poll() is None:
            self.process.terminate()

    def close(self) -> None:
        self.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.thread.join(timeout=1)


def active_profile_chat_ids(
    snapshot: Mapping[str, Any], profile: str
) -> list[str]:
    """Return active Goal-channel chats owned by one sender profile."""

    binding_payloads = snapshot.get("binding_payloads")
    binding_payloads = (
        binding_payloads if isinstance(binding_payloads, Mapping) else {}
    )
    active_target_refs = {
        str(binding.get("target_ref") or "")
        for goal_id, payload in binding_payloads.items()
        if isinstance(payload, Mapping)
        for binding in bindings_for_goal(payload, str(goal_id))
        if binding.get("enabled") is True
    }
    targets = snapshot.get("target_payload")
    targets = targets.get("targets") if isinstance(targets, Mapping) else None
    if not isinstance(targets, Mapping):
        return []
    chats: set[str] = set()
    for target_ref, target in targets.items():
        if not isinstance(target, Mapping) or target.get("enabled") is not True:
            continue
        if str(target_ref) not in active_target_refs:
            continue
        identity = target.get("identity")
        channel = target.get("channel")
        if not isinstance(identity, Mapping) or not isinstance(channel, Mapping):
            continue
        chat_id = str(channel.get("chat_id") or "")
        if (
            str(identity.get("sender_profile") or "") == profile
            and _CHAT_ID.fullmatch(chat_id)
        ):
            chats.add(chat_id)
    return sorted(chats)


def start_team_plan_review_callback_stream(
    *,
    snapshot: Mapping[str, Any],
    profile: str,
    cli_bin: str,
    process_factory: ProcessFactory,
    parent_stop: threading.Event,
    handler: ReviewCallbackHandler,
) -> TeamPlanReviewCallbackStream | None:
    """Start the filtered callback consumer paired with a message stream."""

    chat_ids = active_profile_chat_ids(snapshot, profile)
    if not chat_ids:
        return None
    chat_filter = " or ".join(
        f".chat_id == {json.dumps(chat_id)}" for chat_id in chat_ids
    )
    process = process_factory(
        [
            cli_bin,
            "--profile",
            profile,
            "event",
            "consume",
            "card.action.trigger",
            "--as",
            "bot",
            "--timeout",
            "30m",
            "--max-events",
            "0",
            "--jq",
            f"select({chat_filter})",
        ]
    )
    local_stop = threading.Event()

    def consume() -> None:
        stdout = process.stdout
        if stdout is None:
            return
        for callback_line in stdout:
            if local_stop.is_set() or parent_stop.is_set():
                return
            stripped = callback_line.strip()
            if stripped.startswith(_EVENT_DIAGNOSTIC_PREFIX):
                continue
            try:
                callback_event = json.loads(callback_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(callback_event, Mapping):
                continue
            raw_action = callback_event.get("action_value")
            try:
                callback_action = (
                    json.loads(raw_action)
                    if isinstance(raw_action, str)
                    else raw_action
                )
            except json.JSONDecodeError:
                continue
            if (
                not isinstance(callback_action, Mapping)
                or callback_action.get("schema_version")
                != TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION
            ):
                continue
            try:
                handler(callback_event)
            except (OSError, RuntimeError, TypeError, ValueError):
                logging.getLogger(__name__).warning(
                    "Lark manager review callback was rejected"
                )

    thread = threading.Thread(
        target=consume,
        name=f"loopx-lark-review-callback-{profile}",
        daemon=True,
    )
    thread.start()
    return TeamPlanReviewCallbackStream(
        process=process,
        thread=thread,
        stop=local_stop,
    )


def proposal_ids_after_turn(
    runtime_controller: Any, *, session_id: str, turn_id: str
) -> list[str]:
    """Project canonical team-plan proposal ids emitted by one manager turn."""

    events_after = getattr(runtime_controller.store, "events_after", None)
    if not callable(events_after):
        return []
    proposal_ids: list[str] = []
    for event in events_after(session_id, turn_id, None):
        if (
            not isinstance(event, Mapping)
            or event.get("kind") != "team_plan.projected"
        ):
            continue
        payload = event.get("payload")
        proposal_id = (
            str(payload.get("proposal_id") or "")
            if isinstance(payload, Mapping)
            else ""
        )
        if proposal_id and proposal_id not in proposal_ids:
            proposal_ids.append(proposal_id)
    return proposal_ids


def deliver_team_plan_review_cards_from_runtime(
    *,
    proposal_ids: Sequence[str],
    manager_route: Mapping[str, Any],
    runtime_controller: Any,
    runtime_root: Path,
) -> dict[str, Any]:
    """Resolve the active registry before delivering canonical proposal cards."""

    registry_path = getattr(runtime_controller, "registry_path", None)
    if not isinstance(registry_path, Path):
        raise ValueError("Lark manager proposal delivery requires the active registry")
    return deliver_team_plan_review_cards(
        proposal_ids=proposal_ids,
        manager_route=manager_route,
        registry_path=registry_path,
        runtime_root=runtime_root,
        action_store_root=runtime_root / "chat" / "actions",
    )


def handle_lark_review_callback_for_profile(
    event: Mapping[str, Any],
    *,
    action_service: Any,
    action_store_root: Path,
    profile: str,
    profile_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind a callback to the exact configured sender identity."""

    if not isinstance(profile_config, Mapping):
        raise ValueError("Lark manager profile is unavailable")
    return handle_lark_review_callback(
        event,
        action_service=action_service,
        action_store_root=action_store_root,
        profile_app_id=str(profile_config.get("bot_app_id") or ""),
        cli_bin=str(profile_config.get("cli_bin") or "lark-cli"),
        profile=profile,
    )


def settle_team_plan_proposal_delivery(
    *,
    delivery_state: dict[str, Any],
    delivery_path: Path,
    route: Mapping[str, Any],
    proposal_ids: Sequence[str],
    proposal_deliverer: Callable[
        [Mapping[str, Any], list[str]], Mapping[str, Any]
    ]
    | None,
) -> str | None:
    """Persist one verified dual-audience delivery or return its retry status."""

    normalized_ids = [str(value) for value in proposal_ids]
    if not normalized_ids:
        return None
    if proposal_deliverer is None:
        return "proposal_delivery_unavailable"
    if isinstance(delivery_state.get("proposal_delivery"), Mapping):
        return None
    try:
        receipt = validate_team_plan_delivery_receipt(
            proposal_deliverer(route, normalized_ids),
            proposal_ids=normalized_ids,
        )
    except (OSError, ValueError, TypeError, KeyError):
        return "proposal_delivery_pending"
    delivery_state["proposal_delivery"] = receipt
    delivery_state["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        write_manager_delivery(delivery_path, delivery_state)
    except OSError:
        return "proposal_delivery_receipt_unavailable"
    return None


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolved_binding(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    connection_id: str | None,
    manager_audience: bool,
) -> tuple[dict[str, Any], Path, Path]:
    registry = load_registry(registry_path)
    source = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
        registry=registry,
    )
    source_registry = Path(str(source["source_registry"]))
    binding_path = default_goal_channel_binding_path(source_registry)
    payload = read_goal_channel_binding(binding_path)
    selected_connection_id = connection_id
    if selected_connection_id is None:
        default_binding = binding_for_goal(payload, goal_id)
        if (
            isinstance(default_binding, Mapping)
            and default_binding.get("enabled") is True
            and (
                (default_binding.get("routing") or {}).get("conversation_kind")
                == "manager"
            )
            is manager_audience
        ):
            selected_connection_id = str(
                default_binding.get("connection_id") or ""
            )
        candidates = [
            item
            for item in bindings_for_goal(payload, goal_id)
            if item.get("enabled") is True
            and (
                (item.get("routing") or {}).get("conversation_kind") == "manager"
            )
            is manager_audience
        ]
        if selected_connection_id is None and candidates:
            selected_connection_id = str(
                min(
                    candidates,
                    key=lambda item: str(item.get("connection_id") or ""),
                ).get("connection_id")
                or ""
            )
    raw = binding_for_goal(
        payload, goal_id, connection_id=selected_connection_id
    )
    if raw is None:
        raise ValueError("team plan review audience binding is unavailable")
    if (
        ((raw.get("routing") or {}).get("conversation_kind") == "manager")
        is not manager_audience
    ):
        raise ValueError("team plan review audience kind is unavailable")
    target_path = default_goal_channel_target_path(runtime_root)
    target_ref = str(raw.get("target_ref") or "")
    target = goal_channel_target_for_name(
        read_goal_channel_targets(target_path), target_ref
    )
    if target is None:
        raise ValueError("team plan review audience target is unavailable")
    resolved = binding_for_goal(
        payload,
        goal_id,
        provider_target=target,
        connection_id=selected_connection_id,
    )
    if resolved is None:
        raise ValueError("team plan review audience binding is incomplete")
    return resolved, binding_path, target_path


def _deliver_one(
    *,
    store: ChatActionStore,
    proposal: Mapping[str, Any],
    audience_id: str,
    binding: Mapping[str, Any],
    binding_path: Path,
    target_path: Path,
    authorized_principal: str,
    runner: CommandRunner,
) -> dict[str, Any]:
    audience_goal_id = str(binding.get("goal_id") or "")
    route = goal_channel_delivery_route(
        audience_goal_id, lambda _goal_id: binding
    )
    card = build_team_plan_review_card(proposal, audience_id=audience_id)
    card_digest = _digest(card)
    current = store.load(str(proposal["proposal_id"]))
    review_card = current.get("review_card") if isinstance(current, Mapping) else None
    recorded_deliveries = (
        review_card.get("deliveries") if isinstance(review_card, Mapping) else None
    )
    recorded = (
        recorded_deliveries.get(audience_id)
        if isinstance(recorded_deliveries, Mapping)
        else None
    )
    if isinstance(recorded, Mapping):
        expected = {
            "provider": "lark",
            "chat_id": str(route["chat_id"]),
            "app_id": str(route["bot_app_id"]),
            "cli_bin": str(route["cli_bin"]),
            "sender_profile": str(route["sender_profile"]),
            "binding_digest": goal_channel_binding_digest(binding),
            "card_digest": card_digest,
            "submitted_card": card,
            "authorized_principal": authorized_principal,
        }
        if not _MESSAGE_ID.fullmatch(str(recorded.get("message_id") or "")) or any(
            recorded.get(key) != value for key, value in expected.items()
        ):
            raise ActionConflictError(
                "recorded team plan review audience drifted before retry"
            )
        # The store only records an audience after exact native readback. Reuse
        # that durable checkpoint instead of writing a duplicate actionable
        # card when another audience made the prior attempt partial.
        return {
            "audience_id": audience_id,
            "message_id": str(recorded["message_id"]),
            "external_write_performed": False,
            "readback_verified": True,
        }

    def resolve_current() -> Mapping[str, Any]:
        payload = read_goal_channel_binding(binding_path)
        target_ref = str(binding.get("target_ref") or "")
        target = goal_channel_target_for_name(
            read_goal_channel_targets(target_path), target_ref
        )
        current = binding_for_goal(
            payload,
            str(binding["goal_id"]),
            provider_target=target,
            connection_id=str(binding.get("connection_id") or "") or None,
        )
        if current is None:
            raise ValueError("team plan review audience binding disappeared")
        return current

    session = GoalChannelMessageDeliverySession(
        goal_id=str(binding["goal_id"]),
        binding=binding,
        binding_lock_path=binding_path,
        target_lock_path=target_path,
        history_start_at=str(proposal["created_at"]),
        resolve_current_binding=resolve_current,
        runner=runner,
    )
    if session.verify(route) is not True:
        raise GoalChannelDeliveryStageError(
            "team plan review sender identity could not be verified",
            blocker="sender_identity_unverified",
            failure_stage="verify_sender_identity",
        )
    sent = dict(
        session.send(
            card,
            f"{proposal['proposal_id']}:{audience_id}:{proposal['expected_state_fingerprint']}",
            route,
        )
    )
    message_id = str(sent.get("message_id") or "")
    observed = dict(session.readback(message_id))
    if not (
        observed.get("verified") is True
        and observed.get("message_id") == message_id
        and observed.get("chat_id") == route["chat_id"]
        and observed.get("sender_app_id") == route["bot_app_id"]
    ):
        raise GoalChannelDeliveryStageError(
            "team plan review card lacked exact native readback",
            blocker="delivery_readback_unverified",
            failure_stage="read_review_card",
            external_write_performed=sent.get("external_write_performed") is True,
        )
    store.record_review_card_delivery(
        str(proposal["proposal_id"]),
        audience_id=audience_id,
        delivery={
            "provider": "lark",
            "message_id": message_id,
            "chat_id": str(route["chat_id"]),
            "app_id": str(route["bot_app_id"]),
            "cli_bin": str(route["cli_bin"]),
            "sender_profile": str(route["sender_profile"]),
            "binding_digest": goal_channel_binding_digest(binding),
            "card_digest": card_digest,
            "submitted_card": card,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
            "authorized_principal": authorized_principal,
        },
    )
    return {
        "audience_id": audience_id,
        "message_id": message_id,
        "external_write_performed": sent.get("external_write_performed") is True,
        "readback_verified": True,
    }


def deliver_team_plan_review_cards(
    *,
    proposal_ids: Sequence[str],
    manager_route: Mapping[str, Any],
    registry_path: Path,
    runtime_root: Path,
    action_store_root: Path,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Deliver one proposal to manager and Goal audiences with exact readback."""

    store = ChatActionStore(action_store_root)
    source_sender_id = str(manager_route.get("source_sender_id") or "")
    if not source_sender_id.startswith("ou_"):
        raise ValueError("team plan review requires an authenticated Lark owner")
    authorized_principal = f"lark:{source_sender_id}"
    deliveries: list[dict[str, Any]] = []
    for proposal_id in proposal_ids:
        proposal = store.load(str(proposal_id))
        if proposal is None or proposal.get("action_kind") != "team.plan":
            raise ValueError("typed team plan proposal was not found")
        parameters = proposal.get("normalized_parameters")
        plan_goal_id = (
            str(parameters.get("goal_id") or "")
            if isinstance(parameters, Mapping)
            else ""
        )
        manager_goal_id = str(manager_route.get("goal_id") or "")
        manager_connection_id = str(manager_route.get("connection_id") or "")
        manager_binding, manager_binding_path, target_path = _resolved_binding(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=manager_goal_id,
            connection_id=manager_connection_id or None,
            manager_audience=True,
        )
        goal_binding, goal_binding_path, _ = _resolved_binding(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=plan_goal_id,
            connection_id=None,
            manager_audience=False,
        )
        audiences = [
            (
                "manager",
                manager_binding,
                manager_binding_path,
            ),
            (
                f"goal:{plan_goal_id}",
                goal_binding,
                goal_binding_path,
            ),
        ]
        store.prepare_review_card_delivery(
            str(proposal["proposal_id"]),
            audience_ids=[audience_id for audience_id, _binding, _path in audiences],
            authorized_principal=authorized_principal,
        )
        for audience_id, binding, binding_path in audiences:
            deliveries.append(
                _deliver_one(
                    store=store,
                    proposal=proposal,
                    audience_id=audience_id,
                    binding=binding,
                    binding_path=binding_path,
                    target_path=target_path,
                    authorized_principal=authorized_principal,
                    runner=runner,
                )
            )
    return {
        "schema_version": TEAM_PLAN_DELIVERY_RECEIPT_SCHEMA_VERSION,
        "ok": True,
        "status": "team_plan_review_cards_delivered",
        "proposal_ids": [str(value) for value in proposal_ids],
        "proposal_count": len(proposal_ids),
        "audience_count": len(deliveries),
        "readback_verified": all(
            item.get("readback_verified") is True for item in deliveries
        ),
        "external_write_count": sum(
            item.get("external_write_performed") is True for item in deliveries
        ),
    }


def _callback_action(event: Mapping[str, Any]) -> dict[str, str]:
    if event.get("type") != "card.action.trigger":
        raise ValueError("team plan callback event type is unsupported")
    if event.get("action_tag") != "button":
        raise ValueError("team plan callback must come from a button")
    raw = event.get("action_value")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError("team plan callback action_value is invalid") from exc
    required = {
        "schema_version",
        "proposal_id",
        "state_fingerprint",
        "audience_id",
        "decision",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("team plan callback action is incomplete")
    if value.get("schema_version") != TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION:
        raise ValueError("team plan callback action schema is unsupported")
    if value.get("decision") not in {"confirm", "reject"}:
        raise ValueError("team plan callback decision is unsupported")
    return {key: str(value[key]) for key in value}


def handle_team_plan_review_callback(
    event: Mapping[str, Any],
    *,
    action_service: Any,
    action_store_root: Path,
    profile_app_id: str,
    cli_bin: str,
    profile: str,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Apply one authenticated decision and patch every audience readback."""

    action = _callback_action(event)
    callback_token = str(event.get("token") or "").strip()
    if (
        not callback_token
        or len(callback_token) > 2048
        or any(ord(character) < 32 for character in callback_token)
    ):
        raise ValueError("team plan callback update token is invalid")
    for field, pattern in (
        ("event_id", _EVENT_ID),
        ("message_id", _MESSAGE_ID),
        ("chat_id", _CHAT_ID),
        ("operator_id", _OPEN_ID),
    ):
        if not pattern.fullmatch(str(event.get(field) or "")):
            raise ValueError(f"team plan callback {field} is invalid")
    if str(event.get("host") or "") != "im_message":
        raise ValueError("team plan callback host is unsupported")
    store = ChatActionStore(action_store_root)
    proposal = store.load(action["proposal_id"])
    if proposal is None or proposal.get("action_kind") != "team.plan":
        raise ValueError("team plan callback proposal was not found")
    review_card = proposal.get("review_card")
    deliveries = (
        review_card.get("deliveries")
        if isinstance(review_card, Mapping)
        else None
    )
    delivery = (
        deliveries.get(action["audience_id"])
        if isinstance(deliveries, Mapping)
        else None
    )
    if not isinstance(delivery, Mapping):
        raise ActionConflictError("team plan review card delivery was not recorded")
    if action["state_fingerprint"] != proposal.get("expected_state_fingerprint"):
        raise ActionConflictError("team plan callback state fingerprint drifted")
    if (
        profile_app_id != delivery.get("app_id")
        or cli_bin != delivery.get("cli_bin")
        or profile != delivery.get("sender_profile")
        or str(event["message_id"]) != delivery.get("message_id")
        or str(event["chat_id"]) != delivery.get("chat_id")
    ):
        raise ActionConflictError("team plan callback delivery binding drifted")
    operator_id = str(event["operator_id"])
    existing_confirmation = (
        review_card.get("confirmation")
        if isinstance(review_card, Mapping)
        else None
    )
    settled = isinstance(existing_confirmation, Mapping)
    if not settled:
        expected_card = delivery.get("submitted_card")
        if not isinstance(expected_card, Mapping):
            raise ValueError("team plan submitted card is unavailable")
        if _digest(expected_card) != delivery.get("card_digest"):
            raise ActionConflictError("recorded team plan card digest drifted")
        card_content = event.get("card_content")
        if card_content is None or card_content == "":
            card_content = _read_callback_card_content(
                runner=runner,
                cli_bin=cli_bin,
                profile=profile,
                message_id=str(event["message_id"]),
                chat_id=str(event["chat_id"]),
                app_id=profile_app_id,
            )
        if card_content is None or card_content == "":
            raise ValueError("team plan callback card content is unavailable")
        if not _callback_card_content_matches(card_content, expected_card):
            raise ActionConflictError("team plan callback card content drifted")
    if not _operator_membership_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=profile,
        chat_id=str(event["chat_id"]),
        operator_id=operator_id,
    ):
        raise ActionConflictError(
            "team plan callback tenant membership is unverified"
        )
    decided = store.decide_review_card(
        action["proposal_id"],
        decision=action["decision"],
        confirmation={
            "provider": "lark",
            "event_id": str(event["event_id"]),
            "principal": f"lark:{operator_id}",
            "message_id": str(event["message_id"]),
            "chat_id": str(event["chat_id"]),
            "app_id": profile_app_id,
            "audience_id": action["audience_id"],
            "state_fingerprint": action["state_fingerprint"],
            "card_digest": str(delivery["card_digest"]),
            "confirmed_at": callback_timestamp(
                event.get("timestamp"), subject="team plan"
            ),
        },
    )
    decided_review_card = decided.get("review_card")
    canonical_confirmation = (
        decided_review_card.get("confirmation")
        if isinstance(decided_review_card, Mapping)
        else None
    )
    canonical_decision = (
        str(canonical_confirmation.get("decision") or "")
        if isinstance(canonical_confirmation, Mapping)
        else ""
    )
    if canonical_decision not in {"confirm", "reject"}:
        raise ValueError("team plan canonical decision is unavailable")
    dispatch_lock = store.root / f"{action['proposal_id']}.dispatch.lock"
    with exclusive_file_lock(
        dispatch_lock,
        agent_id="loopx-lark-team-plan",
        operation="dispatch_team_plan",
    ):
        current = store.load(action["proposal_id"])
        if current is None:
            raise ValueError("team plan disappeared before dispatch")
        if (
            current.get("status") == "applying"
            and canonical_decision == "confirm"
        ):
            applied = action_service.apply(action["proposal_id"])
            candidate = applied.get("proposal") if isinstance(applied, Mapping) else None
            if not isinstance(candidate, Mapping):
                raise ValueError("team plan apply returned no canonical proposal")
            current = dict(candidate)
        decided = current
    result_card = build_team_plan_result_card(decided)
    current_review_card = decided.get("review_card")
    current_deliveries = (
        current_review_card.get("deliveries")
        if isinstance(current_review_card, Mapping)
        else None
    )
    if not isinstance(current_deliveries, Mapping):
        raise ValueError("team plan result audiences are unavailable")
    result_verified = True
    for audience_id, raw_delivery in current_deliveries.items():
        if not isinstance(raw_delivery, Mapping):
            result_verified = False
            continue
        if isinstance(raw_delivery.get("result"), Mapping):
            continue
        delivery_profile = str(raw_delivery.get("sender_profile") or "")
        delivery_cli_bin = str(raw_delivery.get("cli_bin") or "")
        if not delivery_profile or not delivery_cli_bin:
            result_verified = False
            continue
        if str(raw_delivery.get("message_id") or "") == str(event["message_id"]):
            update = _update_callback_card(
                runner=runner,
                cli_bin=delivery_cli_bin,
                profile=delivery_profile,
                token=callback_token,
                card=result_card,
                message_id=str(raw_delivery["message_id"]),
                chat_id=str(raw_delivery["chat_id"]),
                app_id=str(raw_delivery["app_id"]),
            )
            transport = "callback_update"
        else:
            update = _patch_operation_result_card(
                runner=runner,
                cli_bin=delivery_cli_bin,
                profile=delivery_profile,
                card=result_card,
                message_id=str(raw_delivery["message_id"]),
                chat_id=str(raw_delivery["chat_id"]),
                app_id=str(raw_delivery["app_id"]),
            )
            transport = "message_patch"
        if update.get("readback_verified") is not True:
            result_verified = False
            continue
        decided = store.record_review_card_result_delivery(
            action["proposal_id"],
            audience_id=str(audience_id),
            result={
                "card_digest": _digest(result_card),
                "transport": transport,
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return {
        "ok": result_verified,
        "schema_version": TEAM_PLAN_CALLBACK_RECEIPT_SCHEMA_VERSION,
        "proposal_id": action["proposal_id"],
        "decision": canonical_decision,
        "proposal_status": decided.get("status"),
        "result_delivery_verified": result_verified,
    }


def handle_lark_review_callback(
    event: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch a Lark review callback without broadening either authority."""

    raw = event.get("action_value")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        value = None
    if (
        isinstance(value, Mapping)
        and value.get("schema_version") == TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION
    ):
        return handle_team_plan_review_callback(event, **kwargs)
    raise ValueError("Lark review callback schema is unsupported")


__all__ = [
    "build_team_plan_result_card",
    "build_team_plan_review_card",
    "deliver_team_plan_review_cards",
    "handle_lark_review_callback",
    "handle_team_plan_review_callback",
    "TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION",
    "TEAM_PLAN_DELIVERY_RECEIPT_SCHEMA_VERSION",
]
