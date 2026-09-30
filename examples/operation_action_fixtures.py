"""Synthetic canonical-operation fixtures shared by examples and unit tests.

Only stdlib and installed LoopX runtime imports belong here. No test framework,
real channel, account, credential or executor is required by the browser smoke.
Policy remains in the existing TypeScript owners; these are storage/wire inputs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService

GOAL_ID = "goal-operation-fixture"
OPERATOR_ID = "ou_authorized_fixture"


def digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def service(
    tmp_path: Path, *, goal_id: str = GOAL_ID
) -> tuple[ChatActionService, ChatActionStore]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "ACTIVE_GOAL_STATE.md").write_text(
        f"---\ngoal_id: {goal_id}\n---\n\n## User Todo\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir()
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": goal_id,
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["finance-fixture-agent"]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = ChatActionStore(tmp_path / "runtime" / "chat" / "actions")
    return ChatActionService(store=store, registry_path=registry), store


def managed_handler(
    service: ChatActionService,
    store: ChatActionStore,
    *,
    goal_id=GOAL_ID,
    session_id="owned-managed-thread",
    profile_digest="c" * 64,
    todo_id="todo-managed",
    model="test-model",
    reasoning_effort="xhigh",
):
    from loopx.control_plane.turn_driver.codex_cli import _store_codex_cli_session
    from loopx.control_plane.turn_driver.codex_operation_host import (
        operation_tool_handler,
    )

    lineage = {
        "goal_id": goal_id,
        "agent_id": "finance-fixture-agent",
        "todo_id": todo_id,
    }
    runtime = store.root.parent.parent
    _store_codex_cli_session(
        runtime,
        lineage=lineage,
        session_id=session_id,
        operation_profile_digest=profile_digest,
        operation_model=model,
        operation_reasoning_effort=reasoning_effort,
    )
    return operation_tool_handler(
        runtime_root=runtime,
        registry_path=service.registry_path,
        lineage=lineage,
        session_id=session_id,
        profile_digest=profile_digest,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def request(
    *, payload: dict[str, object] | None = None, goal_id: str = GOAL_ID
) -> dict[str, object]:
    operation_payload = payload or {
        "schema_version": "finance_order_intent_v0",
        "side": "buy",
        "asset": "SYNTH",
        "quantity": "1.00",
        "order_type": "limit",
        "limit_price": "10.00",
        "time_in_force": "GTC",
        "reduce_only": False,
    }
    return {
        "action_kind": "operation.execute",
        "summary": "Confirm one simulated finance order",
        "idempotency_key": "operation-fixture-v1",
        "context": {"kind": "goal", "goal_id": goal_id},
        "normalized_parameters": {
            "schema_version": "loopx_operation_request_v0",
            "goal_id": goal_id,
            "agent_id": "finance-fixture-agent",
            "domain": "finance",
            "operation_kind": "finance.order.simulate",
            "operation_schema": "finance_order_intent_v0",
            "payload_ref": "finance-order:synthetic-1",
            "payload": operation_payload,
            "payload_digest": digest(operation_payload),
            "projection": {
                "schema_version": "loopx_operation_projection_v0",
                "title": "Simulated trade request",
                "subtitle": "Synthetic fixture · no venue call",
                "focus": "BUY 1.00 SYNTH @ 10.00",
                "fields": [
                    {"label": "Order type", "value": "Limit · GTC"},
                    {"label": "Maximum notional", "value": "10.00 TEST"},
                ],
                "warning": "Simulation only. This cannot submit, sign, or transfer.",
                "simulated": True,
            },
            "destination_account_ref": "account:simulation",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "authorized_principals": [f"lark:{OPERATOR_ID}"],
            "executor": {
                "extension_id": "loopx-finance-execution",
                "protocol": "finance_operation_executor_v0",
                "permission": "finance.operation.simulate",
                "revision": "simulator-v0",
            },
        },
    }


def delivery(proposal: dict[str, object]) -> dict[str, str]:
    assert isinstance(proposal["operation"], dict)
    return {
        "provider": "lark",
        "message_id": "om_operation_fixture",
        "chat_id": "oc_operation_fixture",
        "app_id": "cli_operation_fixture",
        "binding_digest": "a" * 64,
        "card_digest": "b" * 64,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }


def confirmation(
    proposal: dict[str, object], *, event_id: str = "evt-operation-1"
) -> dict[str, str]:
    operation = proposal["operation"]
    assert isinstance(operation, dict)
    delivered = operation["delivery"]
    assert isinstance(delivered, dict)
    return {
        "provider": "lark",
        "event_id": event_id,
        "principal": f"lark:{OPERATOR_ID}",
        "message_id": str(delivered["message_id"]),
        "chat_id": str(delivered["chat_id"]),
        "app_id": str(delivered["app_id"]),
        "surface_kind": "group_message_card",
        "interaction_kind": "button_callback",
        "confirmation_digest": str(operation["confirmation_digest"]),
        "card_digest": str(delivered["card_digest"]),
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }


def agent_result(
    proposal: dict, consumption_id: str, *, result: str = "executed"
) -> dict:
    operation = proposal["operation"]
    return {
        "schema_version": "loopx_operation_outcome_v0",
        "operation_id": proposal["proposal_id"],
        "payload_digest": operation["payload_digest"],
        "confirmation_digest": operation["confirmation_digest"],
        "claim_id": operation["claim"]["claim_id"],
        "executor_revision": operation["executor_revision"],
        "consumption_id": consumption_id,
        "outcome": result,
        "projection_verified": True,
        "simulation": False,
        "external_write_performed": result != "not_executed",
        "evidence_refs": ["receipt:synthetic-fixture-1"],
        "summary": "Synthetic recorded execution evidence.",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
