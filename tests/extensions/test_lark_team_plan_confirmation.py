from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.extensions.lark.team_plan_confirmation import (
    build_team_plan_review_card,
    deliver_team_plan_review_cards,
    handle_team_plan_review_callback,
)


def _proposal(store: ChatActionStore) -> dict[str, Any]:
    return store.create_preview(
        action_kind="team.plan",
        summary="Confirm the team plan",
        normalized_parameters={
            "goal_id": "goal-alpha",
            "plan": {
                "schema_version": "steward_team_plan_preview_v0",
                "kind": "steward_team_plan_preview",
                "goal_id": "goal-alpha",
                "objective": "Ship one bounded intake",
                "lanes": [
                    {
                        "lane_id": "lane-alpha",
                        "agent_id": "agent-alpha",
                        "acceptance": "one canonical Todo exists",
                        "staffing": "ready",
                        "first_todo": {
                            "text": "Implement the intake",
                            "priority": "P1",
                            "task_class": "advancement_task",
                            "action_kind": "implement",
                        },
                    }
                ],
                "quota_envelope": {"slots": 1},
                "stop_condition": "the bounded intake is delivered",
                "applies": False,
            },
        },
        context={"kind": "manager", "goal_id": "goal-alpha"},
        expected_state_fingerprint="state-alpha",
        permission_classification="durable_write",
        validation_evidence=["validated"],
        available_transitions=["apply", "cancel"],
        idempotency_key="team-plan-alpha",
    )


def _digest(card: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            card, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _record_delivery(
    store: ChatActionStore,
    proposal: dict[str, Any],
    *,
    audience_id: str,
    message_id: str,
    chat_id: str,
) -> dict[str, Any]:
    card = build_team_plan_review_card(proposal, audience_id=audience_id)
    store.record_review_card_delivery(
        proposal["proposal_id"],
        audience_id=audience_id,
        delivery={
            "provider": "lark",
            "message_id": message_id,
            "chat_id": chat_id,
            "app_id": "cli_public_fixture",
            "cli_bin": "fake-lark",
            "sender_profile": "fixture",
            "binding_digest": "sha256:" + "b" * 64,
            "card_digest": _digest(card),
            "submitted_card": card,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
            "authorized_principal": "lark:ou_owner",
        },
    )
    return card


def _event(
    *, audience_id: str, message_id: str, chat_id: str, card: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "card.action.trigger",
        "action_tag": "button",
        "action_value": {
            "schema_version": "loopx_team_plan_card_action_v0",
            "proposal_id": "proposal-placeholder",
            "state_fingerprint": "state-alpha",
            "audience_id": audience_id,
            "decision": "confirm",
        },
        "event_id": f"evt_{audience_id.replace(':', '_')}",
        "message_id": message_id,
        "chat_id": chat_id,
        "operator_id": "ou_owner",
        "host": "im_message",
        "token": "callback-token",
        "timestamp": "1789843200000",
        "card_content": card,
    }


def test_two_lark_audiences_apply_one_canonical_team_plan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.extensions.lark.team_plan_confirmation as confirmation

    store = ChatActionStore(tmp_path / "actions")
    proposal = _proposal(store)
    store.prepare_review_card_delivery(
        proposal["proposal_id"],
        audience_ids=["manager", "goal:goal-alpha"],
        authorized_principal="lark:ou_owner",
    )
    manager_card = _record_delivery(
        store,
        proposal,
        audience_id="manager",
        message_id="om_manager",
        chat_id="oc_manager",
    )
    goal_card = _record_delivery(
        store,
        proposal,
        audience_id="goal:goal-alpha",
        message_id="om_goal",
        chat_id="oc_goal",
    )

    monkeypatch.setattr(
        confirmation, "_operator_membership_verified", lambda **_kwargs: True
    )
    monkeypatch.setattr(
        confirmation,
        "_update_callback_card",
        lambda **_kwargs: {
            "external_write_performed": True,
            "readback_verified": True,
        },
    )
    monkeypatch.setattr(
        confirmation,
        "_patch_operation_result_card",
        lambda **_kwargs: {
            "external_write_performed": True,
            "readback_verified": True,
        },
    )

    class ActionService:
        calls = 0

        def apply(self, proposal_id: str) -> dict[str, Any]:
            self.calls += 1
            applied = store.apply(
                proposal_id,
                current_state_fingerprint="state-alpha",
                receipt={
                    "receipt_id": "receipt-alpha",
                    "outcome": "team_plan_applied",
                    "projection_verified": True,
                },
            )
            return {"proposal": applied, "turn": None}

    service = ActionService()
    manager_event = _event(
        audience_id="manager",
        message_id="om_manager",
        chat_id="oc_manager",
        card=manager_card,
    )
    manager_event["action_value"]["proposal_id"] = proposal["proposal_id"]
    first = handle_team_plan_review_callback(
        manager_event,
        action_service=service,
        action_store_root=store.root,
        profile_app_id="cli_public_fixture",
        cli_bin="fake-lark",
        profile="fixture",
    )
    assert first["ok"] is True
    assert first["proposal_status"] == "applied"
    assert service.calls == 1

    goal_event = _event(
        audience_id="goal:goal-alpha",
        message_id="om_goal",
        chat_id="oc_goal",
        card=goal_card,
    )
    goal_event["action_value"]["proposal_id"] = proposal["proposal_id"]
    replay = handle_team_plan_review_callback(
        goal_event,
        action_service=service,
        action_store_root=store.root,
        profile_app_id="cli_public_fixture",
        cli_bin="fake-lark",
        profile="fixture",
    )
    assert replay["ok"] is True
    assert service.calls == 1
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    assert durable["review_card"]["confirmation"]["event_id"] == "evt_manager"
    assert set(durable["review_card"]["deliveries"]) == {
        "manager",
        "goal:goal-alpha",
    }
    assert all(
        delivery.get("result", {}).get("card_digest")
        for delivery in durable["review_card"]["deliveries"].values()
    )


def test_delivery_projects_one_proposal_to_manager_and_goal_audiences(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.extensions.lark.team_plan_confirmation as confirmation

    store = ChatActionStore(tmp_path / "actions")
    proposal = _proposal(store)

    def binding(*, manager: bool) -> dict[str, Any]:
        goal_id = "manager-goal" if manager else "goal-alpha"
        return {
            "goal_id": goal_id,
            "provider": "lark",
            "enabled": True,
            "connection_id": "manager-connection" if manager else "goal-connection",
            "target_ref": "manager-target" if manager else "goal-target",
            "routing": {
                "conversation_kind": "manager" if manager else "goal",
            },
            "channel": {
                "chat_id": "oc_manager" if manager else "oc_goal",
            },
            "identity": {
                "mode": "project_bot",
                "sender_profile": "manager-profile" if manager else "goal-profile",
                "sender_identity": "bot",
                "bot_app_id": "cli_manager" if manager else "cli_goal",
                "bot_display_name": "Manager Bot" if manager else "Goal Bot",
                "cli_bin": "manager-lark" if manager else "goal-lark",
            },
        }

    def resolve_binding(**kwargs: Any):
        selected = binding(manager=kwargs["manager_audience"])
        return selected, tmp_path / "binding.json", tmp_path / "targets.json"

    class DeliverySession:
        def __init__(self, **kwargs: Any) -> None:
            self.binding = kwargs["binding"]
            self.route: Mapping[str, Any] | None = None

        def verify(self, route: Mapping[str, Any]) -> bool:
            self.route = route
            return True

        def send(
            self,
            _card: Mapping[str, Any],
            _key: str,
            route: Mapping[str, Any],
        ) -> dict[str, Any]:
            return {
                "message_id": (
                    "om_manager" if route["chat_id"] == "oc_manager" else "om_goal"
                ),
                "external_write_performed": True,
            }

        def readback(self, message_id: str) -> dict[str, Any]:
            assert self.route is not None
            return {
                "verified": True,
                "message_id": message_id,
                "chat_id": self.route["chat_id"],
                "sender_app_id": self.route["bot_app_id"],
            }

    monkeypatch.setattr(confirmation, "_resolved_binding", resolve_binding)
    monkeypatch.setattr(
        confirmation, "GoalChannelMessageDeliverySession", DeliverySession
    )

    receipt = deliver_team_plan_review_cards(
        proposal_ids=[proposal["proposal_id"]],
        manager_route={
            "goal_id": "manager-goal",
            "connection_id": "manager-connection",
            "source_sender_id": "ou_owner",
        },
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        action_store_root=store.root,
    )

    assert receipt == {
        "schema_version": "lark_team_plan_review_delivery_v0",
        "ok": True,
        "status": "team_plan_review_cards_delivered",
        "proposal_ids": [proposal["proposal_id"]],
        "proposal_count": 1,
        "audience_count": 2,
        "readback_verified": True,
        "external_write_count": 2,
    }
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    assert durable["review_card"]["expected_audience_ids"] == [
        "goal:goal-alpha",
        "manager",
    ]
    assert durable["review_card"]["deliveries"]["manager"]["cli_bin"] == (
        "manager-lark"
    )
    assert durable["review_card"]["deliveries"]["goal:goal-alpha"][
        "sender_profile"
    ] == "goal-profile"


def test_delivery_retry_resumes_after_the_first_audience_checkpoint(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.extensions.lark.team_plan_confirmation as confirmation

    store = ChatActionStore(tmp_path / "actions")
    proposal = _proposal(store)

    def binding(*, manager: bool) -> dict[str, Any]:
        goal_id = "manager-goal" if manager else "goal-alpha"
        return {
            "goal_id": goal_id,
            "provider": "lark",
            "enabled": True,
            "connection_id": "manager-connection" if manager else "goal-connection",
            "target_ref": "manager-target" if manager else "goal-target",
            "routing": {
                "conversation_kind": "manager" if manager else "goal",
            },
            "channel": {
                "chat_id": "oc_manager" if manager else "oc_goal",
            },
            "identity": {
                "mode": "project_bot",
                "sender_profile": "manager-profile" if manager else "goal-profile",
                "sender_identity": "bot",
                "bot_app_id": "cli_manager" if manager else "cli_goal",
                "bot_display_name": "Manager Bot" if manager else "Goal Bot",
                "cli_bin": "manager-lark" if manager else "goal-lark",
            },
        }

    def resolve_binding(**kwargs: Any):
        selected = binding(manager=kwargs["manager_audience"])
        return selected, tmp_path / "binding.json", tmp_path / "targets.json"

    send_calls: list[str] = []
    fail_goal_once = True

    class DeliverySession:
        def __init__(self, **kwargs: Any) -> None:
            self.route: Mapping[str, Any] | None = None

        def verify(self, route: Mapping[str, Any]) -> bool:
            self.route = route
            return True

        def send(
            self,
            _card: Mapping[str, Any],
            _key: str,
            route: Mapping[str, Any],
        ) -> dict[str, Any]:
            nonlocal fail_goal_once
            chat_id = str(route["chat_id"])
            send_calls.append(chat_id)
            if chat_id == "oc_goal" and fail_goal_once:
                fail_goal_once = False
                raise OSError("synthetic second-audience failure")
            return {
                "message_id": ("om_manager" if chat_id == "oc_manager" else "om_goal"),
                "external_write_performed": True,
            }

        def readback(self, message_id: str) -> dict[str, Any]:
            assert self.route is not None
            return {
                "verified": True,
                "message_id": message_id,
                "chat_id": self.route["chat_id"],
                "sender_app_id": self.route["bot_app_id"],
            }

    monkeypatch.setattr(confirmation, "_resolved_binding", resolve_binding)
    monkeypatch.setattr(
        confirmation, "GoalChannelMessageDeliverySession", DeliverySession
    )

    kwargs = {
        "proposal_ids": [proposal["proposal_id"]],
        "manager_route": {
            "goal_id": "manager-goal",
            "connection_id": "manager-connection",
            "source_sender_id": "ou_owner",
        },
        "registry_path": tmp_path / "registry.json",
        "runtime_root": tmp_path,
        "action_store_root": store.root,
    }
    with pytest.raises(OSError, match="second-audience failure"):
        deliver_team_plan_review_cards(**kwargs)

    partial = store.load(proposal["proposal_id"])
    assert partial is not None
    assert set(partial["review_card"]["deliveries"]) == {"manager"}

    receipt = deliver_team_plan_review_cards(**kwargs)

    assert send_calls == ["oc_manager", "oc_goal", "oc_goal"]
    assert receipt["audience_count"] == 2
    assert receipt["external_write_count"] == 1
    assert receipt["readback_verified"] is True
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    assert set(durable["review_card"]["deliveries"]) == {
        "manager",
        "goal:goal-alpha",
    }


def test_recovery_uses_the_first_durable_decision_not_a_later_click(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.extensions.lark.team_plan_confirmation as confirmation

    store = ChatActionStore(tmp_path / "actions")
    proposal = _proposal(store)
    store.prepare_review_card_delivery(
        proposal["proposal_id"],
        audience_ids=["manager", "goal:goal-alpha"],
        authorized_principal="lark:ou_owner",
    )
    manager_card = _record_delivery(
        store,
        proposal,
        audience_id="manager",
        message_id="om_manager",
        chat_id="oc_manager",
    )
    _record_delivery(
        store,
        proposal,
        audience_id="goal:goal-alpha",
        message_id="om_goal",
        chat_id="oc_goal",
    )
    manager_delivery = store.load(proposal["proposal_id"])["review_card"][
        "deliveries"
    ]["manager"]
    store.decide_review_card(
        proposal["proposal_id"],
        decision="confirm",
        confirmation={
            "provider": "lark",
            "event_id": "evt_manager",
            "principal": "lark:ou_owner",
            "message_id": "om_manager",
            "chat_id": "oc_manager",
            "app_id": "cli_public_fixture",
            "audience_id": "manager",
            "state_fingerprint": "state-alpha",
            "card_digest": manager_delivery["card_digest"],
            "confirmed_at": "2026-09-20T00:00:00Z",
        },
    )

    monkeypatch.setattr(
        confirmation, "_operator_membership_verified", lambda **_kwargs: True
    )
    monkeypatch.setattr(
        confirmation,
        "_update_callback_card",
        lambda **_kwargs: {
            "external_write_performed": True,
            "readback_verified": True,
        },
    )
    monkeypatch.setattr(
        confirmation,
        "_patch_operation_result_card",
        lambda **_kwargs: {
            "external_write_performed": True,
            "readback_verified": True,
        },
    )

    class ActionService:
        calls = 0

        def apply(self, proposal_id: str) -> dict[str, Any]:
            self.calls += 1
            return {
                "proposal": store.apply(
                    proposal_id,
                    current_state_fingerprint="state-alpha",
                    receipt={
                        "receipt_id": "receipt-alpha",
                        "outcome": "team_plan_applied",
                        "projection_verified": True,
                    },
                )
            }

    later = _event(
        audience_id="goal:goal-alpha",
        message_id="om_goal",
        chat_id="oc_goal",
        card=manager_card,
    )
    later["event_id"] = "evt_goal_after_confirm"
    later["action_value"]["proposal_id"] = proposal["proposal_id"]
    later["action_value"]["decision"] = "reject"
    later["card_content"] = {"already": "patched"}
    service = ActionService()

    result = handle_team_plan_review_callback(
        later,
        action_service=service,
        action_store_root=store.root,
        profile_app_id="cli_public_fixture",
        cli_bin="fake-lark",
        profile="fixture",
    )

    assert result["decision"] == "confirm"
    assert result["proposal_status"] == "applied"
    assert service.calls == 1
    assert store.load(proposal["proposal_id"])["review_card"]["confirmation"][
        "event_id"
    ] == "evt_manager"
