"""One-operation room claim grants and Lark transport; Todo owns transitions.

Offers are authored by a trusted local Agent CLI. Room membership never grants
claim scope. Private offer files contain only intent/scope and delivery facts;
canonical receipts remain the sole source of accepted work transitions.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ...agent_registry import registered_agent_ids_from_registry
from ...control_plane.coordination.local_authority import claim_canonical_todo_if_promoted
from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ...file_lock import exclusive_file_lock
from ..runtime import default_extension_state_file, resolve_extension_activation
from . import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION
from .card_callback import operator_membership_verified, read_callback_card_content, update_callback_card, patch_result_card
from .goal_channel_delivery_contract import goal_channel_binding_digest, goal_channel_delivery_route
from .goal_channel_message_delivery import GoalChannelMessageDeliverySession, GoalChannelDeliveryStageError
from .goal_channel_work import _resolve_binding, build_work_card, run_goal_channel_work
from .presentation.kanban import CommandRunner, default_subprocess_runner
from .goal_channel_transport import verified_app_id
from .goal_channel_operation import _card_text
from .private_json import write_private_json_atomic

ROOM_CLAIM_OFFER_RECORD_SCHEMA = "loopx_lark_room_claim_offer_record_v0"
ROOM_CLAIM_OFFER_RESULT_SCHEMA = "loopx_lark_room_claim_offer_result_v0"
ROOM_CLAIM_ACTION_SCHEMA = "loopx_room_claim_action_v0"
ROOM_CLAIM_CALLBACK_SCHEMA = "loopx_lark_room_claim_callback_v0"
_OFFER_ID = re.compile(r"^rc_[a-f0-9]{32}$")


class RoomClaimCallbackError(ValueError):
    def __init__(self, code: str, stage: str = "room_claim_scope") -> None:
        super().__init__("Room claim callback could not be admitted or delivered")
        self.code = code
        self.failure_stage = stage


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(root: Path, request_id: str) -> Path:
    if not _OFFER_ID.fullmatch(request_id):
        raise RoomClaimCallbackError("invalid_offer_id")
    return root / "room-claim-offers" / (request_id + ".json")


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise RoomClaimCallbackError("offer_unavailable")
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(p, dict) or p.get("schema_version") != ROOM_CLAIM_OFFER_RECORD_SCHEMA:
            raise ValueError("invalid offer")
        normalized = effect_runtime_result("goal_channel.work.claim_request", p["request"])
        if normalized != p["request"] or p["intent_digest"] != _digest(normalized):
            raise ValueError("intent drift")
        if (p["request_id"] != "rc_" + p["intent_digest"][:32] or path.stem != p["request_id"] or
            p["scope_state"] not in {"active", "revoked"}):
            raise ValueError("scope drift")
        return p
    except RoomClaimCallbackError:
        raise
    except Exception as exc:
        raise RoomClaimCallbackError("offer_unavailable") from exc


def _scope(p: Mapping[str, Any], broker_root: Path) -> dict[str, Any]:
    request = p["request"]
    # The current broker route must still name the original source and provider
    # root. Reconnect cannot silently follow a replacement Goal authority.
    route = resolve_goal_source_runtime_route(registry_path=broker_root / "registry.global.json",
        goal_id=request["goal_id"])
    if (Path(route["source_registry"]).resolve() != Path(p["source_registry"]).resolve() or
        Path(route["source_runtime_root"]).resolve() != Path(p["authority_root"]).resolve()):
        raise RoomClaimCallbackError("source_route_changed")
    with collaboration_goal_scope(Path(p["source_registry"]), goal_id=request["goal_id"],
        agents=(request["actor_id"],), require_active=True) as scope:
        # This facade currently uses the existing goal_id-bound canonical claim
        # wire. An exact-instance profile cannot inherit a legacy offer.
        if scope.exact:
            raise RoomClaimCallbackError("exact_instance_claim_unqualified")
    if request["actor_id"] not in registered_agent_ids_from_registry(Path(p["source_registry"]), request["goal_id"]):
        raise RoomClaimCallbackError("actor_scope_revoked")
    resolve_extension_activation(LARK_EXTENSION_ID,
        state_file=default_extension_state_file(Path(p["authority_root"])),
        required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,))
    binding = _resolve_binding(Path(p["binding_path"]), Path(p["target_path"]), request["goal_id"], request["actor_id"])
    if goal_channel_binding_digest(binding) != p["binding_digest"]:
        raise RoomClaimCallbackError("binding_changed")
    return binding


def build_room_claim_card(request: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    text = f"Task: {_card_text(request['todo_id'])}\nGoal: {_card_text(request['goal_id'])}\nClaim as: {_card_text(request['actor_id'])}"
    return {"schema": "2.0", "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": "Claim task"}},
        "body": {"elements": [{"tag": "markdown", "content": text},
            {"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "Claim task"},
             "behaviors": [{"type": "callback", "value": {"schema_version": ROOM_CLAIM_ACTION_SCHEMA,
                 "request_id": request_id, "intent_digest": _digest(dict(request)), "command": "claim_todo"}}]}]}}


def run_room_claim_offer(*, registry_path: Path, authority_root: Path, broker_root: Path,
    binding_path: Path, target_path: Path, goal_id: str, actor_id: str, command: str,
    execute: bool, todo_id: str | None = None, expected_revision: str | None = None,
    idempotency_key: str | None = None, principals: list[str] | None = None,
    expires_at: str | None = None, request_id: str | None = None,
    runner: CommandRunner = default_subprocess_runner) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": ROOM_CLAIM_OFFER_RESULT_SCHEMA, "ok": False,
        "goal_id": goal_id, "actor_id": actor_id, "operation": "work_" + command, "execute": execute,
        "external_write_performed": False, "readback_verified": False, "canonical_claim_accepted": False,
        "execution_authority_granted": False}
    try:
        validate_public_safe_value({"goal_id": goal_id, "actor_id": actor_id})
        with collaboration_goal_scope(registry_path, goal_id=goal_id, agents=(actor_id,), require_active=True) as scope:
            if scope.exact:
                raise RoomClaimCallbackError("exact_instance_claim_unqualified")
        if actor_id not in registered_agent_ids_from_registry(registry_path, goal_id):
            raise RoomClaimCallbackError("actor_scope_revoked")
        if command == "revoke":
            path = _path(broker_root, str(request_id or ""))
            p = _read(path)
            if p["request"]["goal_id"] != goal_id or p["request"]["actor_id"] != actor_id:
                raise RoomClaimCallbackError("offer_scope_mismatch")
            if execute:
                with exclusive_file_lock(path, operation="room_claim_offer"):
                    p = _read(path)
                    p["scope_state"] = "revoked"
                    write_private_json_atomic(path, p)
            return {**result, "ok": True, "status": "revoked" if execute else "planned", "request_id": p["request_id"]}
        if command != "offer":
            raise RoomClaimCallbackError("unsupported_offer_command")
        if not principals or any(not re.fullmatch(r"lark:ou_[A-Za-z0-9_-]{1,160}", principal) for principal in principals):
            raise RoomClaimCallbackError("invalid_lark_principal")
        request = effect_runtime_result("goal_channel.work.claim_request", {"schema_version": "loopx_room_claim_request_v0",
            "command": "claim_todo", "goal_id": goal_id, "actor_id": actor_id, "todo_id": todo_id,
            "expected_revision": expected_revision, "idempotency_key": idempotency_key,
            "authorized_principals": principals or [], "expires_at": expires_at})
        validate_public_safe_value({k:request[k] for k in ["goal_id", "actor_id", "todo_id", "expected_revision"]})
        admission = effect_runtime_result("goal_channel.work.claim_admission", {"request": request,
            "scope_state": "active", "principal": request["authorized_principals"][0], "observed_at": _now()})
        if not admission["allowed"]:
            raise RoomClaimCallbackError(admission["reason_code"])
        rid = "rc_" + _digest(request)[:32]
        path = _path(broker_root, rid)
        binding = _resolve_binding(binding_path, target_path, goal_id, actor_id)
        p = {"schema_version": ROOM_CLAIM_OFFER_RECORD_SCHEMA, "request_id": rid, "request": request,
            "intent_digest": _digest(request), "scope_state": "active", "source_registry": str(registry_path.resolve()),
            "authority_root": str(authority_root.resolve()), "binding_path": str(binding_path.resolve()),
            "target_path": str(target_path.resolve()), "binding_digest": goal_channel_binding_digest(binding),
            "created_at": _now(), "delivery": None, "last_result_card": None, "result_delivery_verified": False}
        # Validate current canonical eligibility, without creating a claim/receipt.
        preview = claim_canonical_todo_if_promoted(registry_path=registry_path, runtime_root=authority_root,
            goal_id=goal_id, todo_id=request["todo_id"], role="agent", claimed_by=actor_id,
            actor_agent_id=actor_id, dry_run=True, operation_id="offer-preview:" + rid,
            expected_provider_revision=request["expected_revision"])
        if preview is None:
            raise RoomClaimCallbackError("canonical_authority_required")
        card = build_room_claim_card(request, rid)
        result.update(request_id=rid, status="planned", authorized_principal_count=len(request["authorized_principals"]), card=card)
        if not execute:
            return {**result, "ok": True}
        _scope(p, broker_root)
        with exclusive_file_lock(path, operation="room_claim_offer"):
            if path.exists():
                p = _read(path)
                if p["scope_state"] != "active":
                    raise RoomClaimCallbackError("offer_revoked")
            else:
                write_private_json_atomic(path, p)
            binding = _scope(p, broker_root)
            session = GoalChannelMessageDeliverySession(goal_id=goal_id, binding=binding,
                binding_lock_path=binding_path, target_lock_path=target_path, history_start_at=p["created_at"],
                resolve_current_binding=lambda: _scope(p, broker_root), runner=runner)
            route = goal_channel_delivery_route(goal_id, session.resolve)
            if not session.verify(route):
                raise RoomClaimCallbackError("provider_identity_unverified")
            sent = session.send(card, "room-claim-offer:" + rid, route)
            result["external_write_performed"] = sent.get("external_write_performed") is True
            verified = session.readback(str(sent["message_id"])).get("verified") is True
            p["delivery"] = {"message_id": sent["message_id"], "chat_id": route["chat_id"], "app_id": route["bot_app_id"],
                "profile": route["sender_profile"], "card": card, "readback_verified": verified}
            write_private_json_atomic(path, p)
            result.update(ok=verified, status="offered" if verified else "delivery_pending", readback_verified=verified)
            return result
    except GoalChannelDeliveryStageError as exc:
        result.update(blocker=exc.blocker, external_write_performed=exc.external_write_performed is not False)
    except RoomClaimCallbackError as exc:
        result["blocker"] = exc.code
    except Exception:
        result["blocker"] = "offer_unavailable"
    result["status"] = "failed"
    return result


def is_room_claim_callback(event: Mapping[str, Any]) -> bool:
    raw = event.get("action_value")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    return isinstance(value, Mapping) and value.get("schema_version") == ROOM_CLAIM_ACTION_SCHEMA


def handle_room_claim_callback(event: Mapping[str, Any], *, runtime_root: Path,
    profile_app_id: str, cli_bin: str, profile: str,
    runner: CommandRunner = default_subprocess_runner) -> dict[str, Any]:
    try:
        raw = event.get("action_value")
        action = json.loads(raw) if isinstance(raw, str) else raw
        if (event.get("type") != "card.action.trigger" or event.get("action_tag") != "button" or
            event.get("host") != "im_message" or not isinstance(action, Mapping) or
            set(action) != {"schema_version", "request_id", "intent_digest", "command"} or
            action["schema_version"] != ROOM_CLAIM_ACTION_SCHEMA or action["command"] != "claim_todo"):
            raise RoomClaimCallbackError("invalid_callback")
        token = str(event.get("token") or "")
        if not token or len(token) > 2048 or any(ord(c) < 32 for c in token):
            raise RoomClaimCallbackError("invalid_callback_token")
        path = _path(runtime_root, str(action["request_id"]))
        p = _read(path)
        with exclusive_file_lock(path, operation="room_claim_callback"):
            p = _read(path)
            request = p["request"]
            delivery = p.get("delivery")
            principal = "lark:" + str(event.get("operator_id") or "")
            admission = effect_runtime_result("goal_channel.work.claim_admission", {"request": request,
                "scope_state": p["scope_state"], "principal": principal, "observed_at": _now()})
            if not admission["allowed"]:
                raise RoomClaimCallbackError(admission["reason_code"])
            if (action["intent_digest"] != p["intent_digest"] or not isinstance(delivery, Mapping) or
                delivery.get("readback_verified") is not True or event.get("message_id") != delivery["message_id"] or
                event.get("chat_id") != delivery["chat_id"] or profile_app_id != delivery["app_id"] or profile != delivery["profile"]):
                raise RoomClaimCallbackError("callback_delivery_scope_mismatch")
            binding = _scope(p, runtime_root)
            route = goal_channel_delivery_route(request["goal_id"], lambda _: binding)
            if route["bot_app_id"] != profile_app_id or route["sender_profile"] != profile:
                raise RoomClaimCallbackError("callback_provider_scope_mismatch")
            if not operator_membership_verified(runner=runner, cli_bin=cli_bin, profile=profile,
                chat_id=delivery["chat_id"], operator_id=str(event["operator_id"])):
                raise RoomClaimCallbackError("operator_identity_unverified")
            # Always hydrate the originating Bot message. Visible-only equality
            # cannot prove this action's hidden request identity.
            observed = read_callback_card_content(runner=runner, cli_bin=cli_bin, profile=profile,
                message_id=delivery["message_id"], chat_id=delivery["chat_id"], app_id=profile_app_id)
            if isinstance(observed, str):
                observed = json.loads(observed)
            if observed != delivery["card"] and observed != p.get("last_result_card"):
                raise RoomClaimCallbackError("callback_card_drifted")
            claimed = run_goal_channel_work(registry_path=Path(p["source_registry"]),
                runtime_root=Path(p["authority_root"]), binding_path=Path(p["binding_path"]), target_path=Path(p["target_path"]),
                goal_id=request["goal_id"], actor_id=request["actor_id"], command="claim", execute=True,
                todo_id=request["todo_id"], expected_revision=request["expected_revision"],
                idempotency_key=request["idempotency_key"], runner=runner, publish_receipt=False)
            card = build_work_card(claimed) if claimed.get("projection") else {
                "config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "Claim readback"}},
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": str(claimed.get("public_summary"))}}]}
            p["last_result_card"] = card
            p["result_delivery_verified"] = False
            write_private_json_atomic(path, p)
            _scope(p, runtime_root)
            if verified_app_id(runner=runner, cli_bin=cli_bin, profile=profile) != profile_app_id:
                raise RoomClaimCallbackError("callback_provider_scope_mismatch")
            updated = update_callback_card(runner=runner, cli_bin=cli_bin, profile=profile, token=token,
                card=card, message_id=delivery["message_id"], chat_id=delivery["chat_id"], app_id=profile_app_id)
            p["result_delivery_verified"] = updated.get("readback_verified") is True
            write_private_json_atomic(path, p)
            return {"schema_version": ROOM_CLAIM_CALLBACK_SCHEMA, "ok": p["result_delivery_verified"],
                "request_id": p["request_id"], "claim_status": claimed["status"],
                "canonical_claim_accepted": claimed["canonical_claim_accepted"],
                "receipt": claimed.get("receipt"), "card_update_verified": p["result_delivery_verified"],
                "external_write_performed": updated.get("external_write_performed"),
                "callback_ack_is_execution_receipt": False, "execution_authority_granted": False}
    except RoomClaimCallbackError:
        raise
    except Exception as exc:
        raise RoomClaimCallbackError("callback_unavailable") from exc


def recover_room_claim_results(*, runtime_root: Path, profile_app_id: str, allowed_chat_ids: set[str],
    cli_bin: str, profile: str, runner: CommandRunner = default_subprocess_runner, limit: int = 20) -> dict[str, int]:
    """Retry only recorded public result-card transport, never a work transition."""
    stats = {"attempted": 0, "delivered": 0, "failed": 0}
    for path in sorted((runtime_root / "room-claim-offers").glob("rc_*.json")):
        if stats["attempted"] >= min(max(limit, 1), 100):
            break
        try:
            p = _read(path)
            d = p.get("delivery")
            if (not p.get("last_result_card") or p.get("result_delivery_verified") is True or
                not isinstance(d, Mapping) or d["app_id"] != profile_app_id or
                d["chat_id"] not in allowed_chat_ids or d["profile"] != profile):
                continue
            stats["attempted"] += 1
            with exclusive_file_lock(path, operation="room_claim_result_recovery"):
                p = _read(path)
                d = p["delivery"]
                _scope(p, runtime_root)
                if p.get("scope_state") != "active" or p.get("result_delivery_verified") is True:
                    continue
                request = p["request"]
                admission = effect_runtime_result("goal_channel.work.claim_admission", {"request": request,
                    "scope_state": p["scope_state"], "principal": request["authorized_principals"][0], "observed_at": _now()})
                if not admission["allowed"] or verified_app_id(runner=runner, cli_bin=cli_bin, profile=profile) != profile_app_id:
                    continue
                result = patch_result_card(runner=runner, cli_bin=cli_bin, profile=profile,
                    card=p["last_result_card"], message_id=d["message_id"], chat_id=d["chat_id"], app_id=profile_app_id)
                p["result_delivery_verified"] = result.get("readback_verified") is True
                write_private_json_atomic(path, p)
                stats["delivered" if p["result_delivery_verified"] else "failed"] += 1
        except Exception:
            stats["failed"] += 1
    return stats
