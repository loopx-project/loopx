from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any

import pytest
from argparse import Namespace
import sys

from loopx.chat_action_store import ActionConflictError, ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.cli_commands.goal_channel_operation import (
    GoalChannelOperationContext,
    _prepare_goal_channel_operation,
    run_goal_channel_operation,
)
from loopx.control_plane.collaboration.operation_handoff import agent_operation_action
from loopx.control_plane.collaboration.inbox import pending
from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    write_goal_channel_binding,
)
from loopx.extensions.lark.goal_channel_message_delivery import (
    GoalChannelDeliveryStageError,
    message_card_matches,
    normalized_card_text,
)
from loopx.extensions.lark.goal_channel_operation import (
    OperationExecutorDriftError,
    build_goal_channel_operation_card,
    build_goal_channel_operation_result_card,
    deliver_goal_channel_operation_card,
    handle_goal_channel_operation_callback,
    recover_goal_channel_operation_results,
    recover_goal_channel_simulation_claims,
)
from loopx.extensions.lark import goal_channel_operation
from loopx.extensions.lark.goal_channel_targets import add_lark_goal_channel_target


GOAL_ID = "goal-operation-card-fixture"
AGENT_ID = "finance-operation-agent"
OPERATOR_ID = "ou_operation_owner"
CHAT_ID = "oc_operation_fixture"
APP_ID = "cli_operation_fixture"
TENANT_KEY = "tenant_operation_fixture"


def _prepare_agent_handoff(
    store: ChatActionStore, registry: Path, *, managed: bool = False
) -> dict[str, Any]:
    baseline = _prepare(store, registry)
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["thread_agent_bindings"] = [
        {
            "agent_id": AGENT_ID,
            "host_surface": "codex-app",
            "thread_id": "thread-operation-fixture",
        }
    ]
    registry.write_text(json.dumps(data))
    parameters = dict(baseline["normalized_parameters"])
    parameters.pop("projection_digest")
    parameters["executor"] = {
        "kind": "agent_session",
        "host_surface": "codex-app",
        "thread_id": "thread-operation-fixture",
        "revision": "agent-session-handoff-v0",
    }
    parameters["operation_kind"] = "fixture.submit"
    if managed:
        from loopx.control_plane.turn_driver.codex_cli import _store_codex_cli_session

        _store_codex_cli_session(
            store.root.parent.parent,
            lineage={
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "todo_id": "todo-managed",
            },
            session_id="owned-managed-thread",
            operation_profile_digest="c" * 64,
            operation_model="test-model",
            operation_reasoning_effort="xhigh",
        )
        parameters["executor"] = {
            "kind": "managed_turn",
            "todo_id": "todo-managed",
            "session_id": "owned-managed-thread",
            "profile_digest": "c" * 64,
            "model": "test-model",
            "reasoning_effort": "xhigh",
            "revision": "managed-turn-handoff-v0",
        }
    parameters["projection"] = {
        **parameters["projection"],
        "simulated": False,
        "title": "Synthetic Agent execution handoff",
        "warning": "Engineering fixture; never sent to a live provider.",
    }
    parameters["destination_account_ref"] = "account:synthetic-fixture"
    return ChatActionService(store=store, registry_path=registry).preview(
        {
            "action_kind": "operation.execute",
            "summary": "Synthetic original-Agent handoff",
            "idempotency_key": "agent-operation-fixture-v1",
            "context": {"kind": "goal", "goal_id": GOAL_ID},
            "normalized_parameters": parameters,
        }
    )


@pytest.mark.parametrize("managed", [False, True])
def test_authenticated_callback_hands_off_without_calling_any_executor_and_reconciles_original_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed: bool,
) -> None:
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-operation-fixture")
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare_agent_handoff(store, registry, managed=managed)
    cards: dict[str, dict[str, Any]] = {}
    runner = _runner([], cards)
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
    )
    delivered = store.load(proposal["proposal_id"])
    card = cards[delivered["operation"]["delivery"]["message_id"]]
    event = _event(delivered, card)

    def no_executor(_proposal):
        pytest.fail("a human callback must not run an Agent or simulation executor")

    kwargs = dict(
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=no_executor,
    )
    for invalid in [
        {"operator_id": "ou_wrong_operator"},
        {"message_id": "om_wrong_card"},
    ]:
        with pytest.raises(ActionConflictError):
            handle_goal_channel_operation_callback({**event, **invalid}, **kwargs)
    first = handle_goal_channel_operation_callback(event, **kwargs)
    replay = handle_goal_channel_operation_callback(event, **kwargs)
    assert first["status"] == replay["status"] == "authorization_pending"
    assert first["outcome"] is None and not first["domain_external_write_performed"]
    assert first["callback_ack_is_execution_receipt"] is False
    claimed = store.load(proposal["proposal_id"])
    assert claimed["operation"]["result_delivery"] is None
    assert (
        "等待绑定的受管回合" if managed else "原宿主身份认证尚未接通"
    ) in normalized_card_text(next(iter(cards.values())))
    actor = {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "host_surface": "codex-app",
        "thread_id": "thread-operation-fixture",
    }
    context = GoalChannelOperationContext(runtime, registry, runtime, binding)
    if managed:
        actor.update(
            host_surface="loopx-managed-codex",
            thread_id="owned-managed-thread",
            todo_id="todo-managed",
            profile_digest="c" * 64,
            host_turn_id="native-turn-1",
            model="test-model",
            reasoning_effort="xhigh",
        )
    args = Namespace(
        goal_channel_command="consume-operation",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        proposal_id=proposal["proposal_id"],
        host_surface="codex-app",
        thread_id="thread-operation-fixture",
        consumption_id="attempt-1",
        execute=False,
    )
    dry = run_goal_channel_operation(args, context=context)
    assert dry["ok"] is False and not store.load(proposal["proposal_id"])[
        "operation"
    ].get("agent_handoff")
    args.execute = True
    before = store.path.read_bytes()
    for ambient_thread in ["", "thread-unrelated-fixture", "thread-operation-fixture"]:
        monkeypatch.setenv("CODEX_THREAD_ID", ambient_thread)
        rejected = run_goal_channel_operation(args, context=context)
        assert rejected["ok"] is False
        assert rejected["blocker"] == "operation_host_authentication_unavailable"
        assert store.path.read_bytes() == before
    # The internal IO fixture qualifies one-shot/reconciliation semantics,
    # not a production host issuer. The public CLI above must remain blocked.
    consumed = agent_operation_action(
        runtime,
        registry,
        proposal_id=proposal["proposal_id"],
        actor=actor,
        action="consume",
        consumption_id="attempt-1",
    )
    assert consumed["execution_allowed"] is True
    assert (
        agent_operation_action(
            runtime,
            registry,
            proposal_id=proposal["proposal_id"],
            actor=actor,
            action="consume",
            consumption_id="attempt-1",
        )["execution_allowed"]
        is False
    )
    operation = claimed["operation"]
    unknown = {
        "schema_version": "loopx_operation_outcome_v0",
        "operation_id": proposal["proposal_id"],
        "payload_digest": operation["payload_digest"],
        "confirmation_digest": operation["confirmation_digest"],
        "claim_id": operation["claim"]["claim_id"],
        "executor_revision": operation["executor_revision"],
        "consumption_id": "attempt-1",
        "projection_verified": True,
        "simulation": False,
        "outcome": "submission_unknown",
        "external_write_performed": True,
        "summary": "Synthetic submission result is unknown; do not resubmit.",
        "evidence_refs": ["receipt:unknown-fixture"],
    }
    reported = agent_operation_action(
        runtime,
        registry,
        proposal_id=proposal["proposal_id"],
        actor=actor,
        action="report",
        outcome=unknown,
    )
    assert (
        reported["status"] == "submission_unknown" and reported["needs_reconciliation"]
    )
    assert pending(runtime, GOAL_ID, AGENT_ID)["operation_handoffs"][0][
        "needs_reconciliation"
    ]
    recovered = recover_goal_channel_operation_results(
        action_store_root=store.root,
        profile_app_id=APP_ID,
        allowed_chat_ids={CHAT_ID},
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
    )
    assert recovered["delivered"] == 1
    assert "不可重复提交" in normalized_card_text(next(iter(cards.values())))
    final = {
        **unknown,
        "outcome": "not_executed",
        "external_write_performed": False,
        "summary": "Synthetic original venue evidence proves no submission.",
        "reconciles_outcome_digest": _digest(unknown),
        "evidence_refs": ["receipt:reconciled-fixture"],
    }
    assert (
        agent_operation_action(
            runtime,
            registry,
            proposal_id=proposal["proposal_id"],
            actor=actor,
            action="report",
            outcome=final,
        )["outcome"]
        == final
    )
    updated = store.load(proposal["proposal_id"])
    frame = goal_channel_operation._operation_review_frame(updated)
    assert frame["resultDeliveryVerified"] is False
    assert frame["resultKind"] == "not_executed"
    recovered = recover_goal_channel_operation_results(
        action_store_root=store.root,
        profile_app_id=APP_ID,
        allowed_chat_ids={CHAT_ID},
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
    )
    assert recovered["delivered"] == 1
    assert "已结束，未执行" in normalized_card_text(next(iter(cards.values())))
    assert store.load(proposal["proposal_id"])["operation"]["outcome"] == unknown
    assert (
        store.load(proposal["proposal_id"])["operation"]["result_delivery"][
            "outcome_stage"
        ]
        == "reconciled"
    )
    assert (
        agent_operation_action(
            runtime,
            registry,
            proposal_id=proposal["proposal_id"],
            actor=actor,
            action="consume",
            consumption_id="attempt-1",
        )["execution_allowed"]
        is False
    )
    assert "operation_handoffs" not in pending(runtime, GOAL_ID, AGENT_ID)
    assert (
        agent_operation_action(
            runtime,
            registry,
            proposal_id=proposal["proposal_id"],
            actor=actor,
            action="inspect",
        )["execution_allowed"]
        is False
    )


def test_real_cli_rejects_same_user_environment_forgery_before_private_reads_or_writes(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare_agent_handoff(store, registry)
    cards: dict[str, dict[str, Any]] = {}
    runner = _runner([], cards)
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
    )
    delivered = store.load(proposal["proposal_id"])
    handle_goal_channel_operation_callback(
        _event(delivered, cards[delivered["operation"]["delivery"]["message_id"]]),
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=lambda _: pytest.fail("no external execution"),
    )
    assert (
        store.load(proposal["proposal_id"])["operation"]["lifecycle_state"] == "claimed"
    )
    before = store.path.read_bytes()
    command_args = {
        "consume-operation": ["--consumption-id", "attempt-forged", "--execute"],
        "inspect-operation": [],
        # Missing outcome is intentional: authentication must precede its read.
        "report-operation": [
            "--outcome-json",
            str(tmp_path / "absent-outcome.json"),
            "--execute",
        ],
    }
    for command, extra in command_args.items():
        for ambient_thread in [
            "thread-operation-fixture",
            "thread-unrelated-fixture",
            "",
        ]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "loopx.cli",
                    "--format",
                    "json",
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "goal-channel",
                    command,
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    AGENT_ID,
                    "--proposal-id",
                    proposal["proposal_id"],
                    "--host-surface",
                    "codex-app",
                    "--thread-id",
                    "thread-operation-fixture",
                    *extra,
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
                env={**os.environ, "CODEX_THREAD_ID": ambient_thread},
            )
            assert result.returncode == 1, result.stdout + result.stderr
            packet = json.loads(result.stdout)
            assert packet["blocker"] == "operation_host_authentication_unavailable"
            assert packet["details"]["execution_allowed"] is False
            assert packet["external_write_performed"] is False
            assert (
                "environment ids and route flags are not identity proof"
                in packet["public_summary"]
            )
            assert "normalized_parameters" not in packet and "outcome" not in packet
            assert store.path.read_bytes() == before


def test_cli_host_gate_survives_unexpected_success_from_an_older_effect_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.cli_commands import goal_channel_operation as command_module

    store, registry, runtime, binding, _target = _fixture(tmp_path)
    proposal = _prepare_agent_handoff(store, registry)
    before = store.path.read_bytes()
    monkeypatch.setattr(
        command_module,
        "effect_runtime_result",
        lambda *_: {
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "host_surface": "codex-app",
            "thread_id": "thread-operation-fixture",
        },
    )
    args = Namespace(
        goal_channel_command="consume-operation",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        proposal_id=proposal["proposal_id"],
        host_surface="codex-app",
        thread_id="thread-operation-fixture",
        consumption_id="attempt-legacy",
        execute=True,
    )
    packet = run_goal_channel_operation(
        args,
        context=GoalChannelOperationContext(runtime, registry, runtime, binding),
    )
    assert packet["ok"] is False and packet["external_write_performed"] is False
    assert "no authenticated host transport" in packet["public_summary"]
    assert store.path.read_bytes() == before


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_internal_replacement_io_fixture_recovers_unknown_without_qualifying_host_identity(
    tmp_path: Path,
) -> None:
    from loopx.thread_agent_binding import (
        bind_thread_agent_in_registry,
        unbind_thread_agent_in_registry,
    )

    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare_agent_handoff(store, registry)
    cards: dict[str, dict[str, Any]] = {}
    runner = _runner([], cards)
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
    )
    delivered = store.load(proposal["proposal_id"])
    message_id = delivered["operation"]["delivery"]["message_id"]
    handle_goal_channel_operation_callback(
        _event(delivered, cards[message_id]),
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=lambda _: pytest.fail("recovery fixture must not execute externally"),
    )
    original = {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "host_surface": "codex-app",
        "thread_id": "thread-operation-fixture",
    }
    replacement = {**original, "thread_id": "thread-replacement-fixture"}

    def cli(*arguments: str, thread: str, expected_exit: int = 0) -> dict[str, Any]:
        # Only the locator-only Inbox read remains public here. Positive
        # operation calls below exercise internal IO, not host authentication.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                *arguments,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env={**os.environ, "CODEX_THREAD_ID": thread},
        )
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout)

    def operation(actor: dict[str, str], action: str, **request: Any):
        return agent_operation_action(
            runtime,
            registry,
            proposal_id=proposal["proposal_id"],
            actor=actor,
            action=action,
            **request,
        )

    consumed = operation(original, "consume", consumption_id="attempt-1")
    assert consumed["execution_allowed"]
    unknown = {
        "schema_version": "loopx_operation_outcome_v0",
        **{
            key: consumed[key]
            for key in (
                "operation_id",
                "payload_digest",
                "confirmation_digest",
                "claim_id",
                "executor_revision",
            )
        },
        "consumption_id": "attempt-1",
        "projection_verified": True,
        "simulation": False,
        "outcome": "submission_unknown",
        "external_write_performed": True,
        "summary": "Synthetic unknown submission requires original-system evidence.",
        "evidence_refs": ["receipt:unknown-fixture"],
    }
    operation(original, "report", outcome=unknown)
    unbind_thread_agent_in_registry(registry_path=registry, **original, execute=True)
    bind_thread_agent_in_registry(registry_path=registry, **replacement, execute=True)
    inbox = cli(
        "manager-inbox",
        "read",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        thread=replacement["thread_id"],
    )
    assert inbox["operation_handoffs"][0]["binding_current"] is False
    inspected = operation(replacement, "inspect")
    assert inspected["outcome"] == unknown and inspected["route"] == original
    assert inspected["access"]["owner"] == replacement
    assert inspected["access"]["permission"] == "historical_evidence_only"
    before = store.path.read_bytes()
    with pytest.raises(ActionConflictError):
        operation(replacement, "consume", consumption_id="attempt-2")
    assert store.path.read_bytes() == before
    final = {
        **unknown,
        "outcome": "not_executed",
        "external_write_performed": False,
        "summary": "Synthetic original-system evidence proves no submission.",
        "evidence_refs": ["receipt:reconciled-fixture"],
    }
    with pytest.raises(ActionConflictError):
        operation(replacement, "report", outcome=final)
    assert store.path.read_bytes() == before
    final["reconciles_outcome_digest"] = _digest(unknown)
    dry = operation(replacement, "inspect")
    assert not dry["execution_allowed"] and store.path.read_bytes() == before
    reported = operation(replacement, "report", outcome=final)
    assert not reported["execution_allowed"] and not reported["needs_reconciliation"]
    assert reported["reconciliation_report"]["owner"] == replacement
    inspected = operation(replacement, "inspect")
    assert inspected["reconciliation"] == final and inspected["outcome"] == unknown
    assert inspected["consumption"]["route"] == original
    assert inspected["reconciliation_report"]["original_route"] == original
    frame = goal_channel_operation._operation_review_frame(
        store.load(proposal["proposal_id"])
    )
    assert frame["resultKind"] == "not_executed" and not frame["resultDeliveryVerified"]
    recovered = recover_goal_channel_operation_results(
        action_store_root=store.root,
        profile_app_id=APP_ID,
        allowed_chat_ids={CHAT_ID},
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
    )
    assert recovered["delivered"] == 1 and set(cards) == {message_id}
    assert "已结束，未执行" in normalized_card_text(cards[message_id])
    updated = store.load(proposal["proposal_id"])
    assert updated["operation"]["outcome"] == unknown
    assert updated["operation"]["result_delivery"]["outcome_stage"] == "reconciled"
    assert goal_channel_operation._operation_review_frame(updated)[
        "resultDeliveryVerified"
    ]


def _fixture(
    tmp_path: Path,
) -> tuple[ChatActionStore, Path, Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        f"---\ngoal_id: {GOAL_ID}\n---\n\n## User Todo\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {"registered_agents": [AGENT_ID]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    target_path = runtime_root / "goal-channel-targets.json"
    add_lark_goal_channel_target(
        target_path=target_path,
        target_name="operation-route",
        chat_id=CHAT_ID,
        chat_name="Operation Fixture",
        identity_mode="project_bot",
        sender_profile="operation-bot",
        sender_identity="bot",
        bot_app_id=APP_ID,
        bot_display_name="Operation Bot",
        cli_bin="lark-cli",
        execute=True,
    )
    binding_path = registry_path.parent / "goal-channel.json"
    write_goal_channel_binding(
        binding_path,
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {
                GOAL_ID: {
                    "goal_id": GOAL_ID,
                    "provider": "lark",
                    "enabled": True,
                    "agent_id": AGENT_ID,
                    "target_ref": "operation-route",
                    "channel": {},
                    "identity": {},
                }
            },
        },
    )
    store = ChatActionStore(runtime_root / "chat" / "actions")
    return store, registry_path, runtime_root, binding_path, target_path


def _prepare(store: ChatActionStore, registry_path: Path) -> dict[str, Any]:
    payload = {
        "schema_version": "finance_order_intent_v0",
        "asset": "SYNTH",
        "side": "buy",
        "quantity": "1.00",
        "quantity_unit": "SYNTH",
        "order_type": "limit",
        "limit_price": "10.00",
        "price_unit": "TEST",
        "time_in_force": "GTC",
        "reduce_only": False,
        "maximum_fee": "0.10",
        "fee_unit": "TEST",
    }
    service = ChatActionService(store=store, registry_path=registry_path)
    return service.preview(
        {
            "action_kind": "operation.execute",
            "summary": "Confirm one simulated finance order",
            "idempotency_key": "operation-card-fixture-v1",
            "context": {"kind": "goal", "goal_id": GOAL_ID},
            "normalized_parameters": {
                "schema_version": "loopx_operation_request_v0",
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "domain": "finance",
                "operation_kind": "finance.order.simulate",
                "operation_schema": "finance_order_intent_v0",
                "payload_ref": "finance-order:operation-card-fixture",
                "payload": payload,
                "payload_digest": _digest(payload),
                "projection": {
                    "schema_version": "loopx_operation_projection_v0",
                    "title": "Simulated trade request",
                    "subtitle": "Synthetic fixture · no venue call",
                    "focus": "BUY 1.00 SYNTH @ 10.00 TEST",
                    "fields": [
                        {"label": "Order", "value": "Limit · GTC"},
                        {"label": "Maximum fee", "value": "0.10 TEST"},
                    ],
                    "warning": (
                        "Simulation only. This card cannot submit, sign, or transfer."
                    ),
                    "simulated": True,
                },
                "destination_account_ref": "account:simulation",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "authorized_principals": [f"lark:{OPERATOR_ID}"],
                "executor": {
                    "extension_id": "loopx-finance-execution",
                    "protocol": "finance_operation_executor_v0",
                    "permission": "finance.operation.simulate",
                    "revision": "simulator-v0",
                },
            },
        }
    )


def _normalized_card_v2(card: Mapping[str, Any]) -> str:
    header = card["header"]
    lines = [
        (
            f'<card title="{header["title"]["content"]}" '
            f'subtitle="{header["subtitle"]["content"]}">'
        )
    ]
    lines.extend(f"「{item['text']['content']}」" for item in header["text_tag_list"])
    for element in card["body"]["elements"]:
        columns = element["columns"]
        buttons = [
            column["elements"][0]["text"]["content"]
            for column in columns
            if column["elements"][0]["tag"] == "button"
        ]
        if buttons:
            lines.append(" ".join(f"[{label}]" for label in buttons))
            continue
        lines.extend(column["elements"][0]["content"] for column in columns)
    lines.append("</card>")
    return "\n".join(lines)


def test_normalized_action_card_cannot_prove_historical_action_identity() -> None:
    card = {
        "schema": "2.0",
        "header": {
            "title": {"content": "Simulated trade request"},
            "subtitle": {"content": "Synthetic fixture · no venue call"},
            "text_tag_list": [],
        },
        "body": {
            "elements": [
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"content": "Confirm"},
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {
                                                "operation_id": (
                                                    "proposal-normalized-binding"
                                                )
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }
    normalized = {"content": normalized_card_text(card)}

    assert message_card_matches(normalized, card)
    assert not message_card_matches(normalized, card, allow_normalized=False)


def _runner(
    calls: list[list[str]],
    sent_cards: dict[str, dict[str, Any]],
    *,
    normalized_card_v2_readback: bool = False,
):
    def run(
        args: list[str], _cwd: Path | None, _timeout: float | None
    ) -> dict[str, Any]:
        calls.append(args)
        if "auth" in args and "status" in args:
            payload = {
                "ok": True,
                "appId": APP_ID,
                "identities": {
                    "bot": {
                        "available": True,
                        "verified": True,
                        "appName": "Operation Bot",
                    }
                },
            }
        elif "chats" in args and "get" in args:
            payload = {
                "ok": True,
                "data": {"chat_id": CHAT_ID, "tenant_key": TENANT_KEY},
            }
        elif "+chat-members-list" in args:
            if args[args.index("--member-types") + 1] == "bot":
                payload = {"ok": True, "data": {"bots": [{"app_id": APP_ID}]}}
            else:
                payload = {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "member_id": OPERATOR_ID,
                                "tenant_key": TENANT_KEY,
                            }
                        ]
                    },
                }
        elif "+chat-messages-list" in args:
            payload = {
                "ok": True,
                "has_more": False,
                "messages": [
                    {
                        "message_id": message_id,
                        "chat_id": CHAT_ID,
                        "sender": {"sender_type": "app", "id": APP_ID},
                        "deleted": False,
                        **(
                            {"content": _normalized_card_v2(card)}
                            if normalized_card_v2_readback
                            else {"body": {"content": json.dumps(card)}}
                        ),
                    }
                    for message_id, card in sent_cards.items()
                ],
            }
        elif "+messages-send" in args:
            message_id = "om_operation_card_fixture"
            sent_cards[message_id] = json.loads(args[args.index("--content") + 1])
            payload = {"ok": True, "data": {"message_id": message_id}}
        elif "+messages-mget" in args:
            message_id = args[args.index("--message-ids") + 1]
            payload = {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "message_id": message_id,
                            "chat_id": CHAT_ID,
                            "sender": {"sender_type": "app", "id": APP_ID},
                            **(
                                {"content": _normalized_card_v2(sent_cards[message_id])}
                                if normalized_card_v2_readback
                                else {
                                    "body": {
                                        "content": json.dumps(sent_cards[message_id])
                                    }
                                }
                            ),
                        }
                    ]
                },
            }
        elif (
            "api" in args
            and "GET" in args
            and any("/open-apis/im/v1/messages/" in item for item in args)
        ):
            endpoint = next(
                item for item in args if "/open-apis/im/v1/messages/" in item
            )
            message_id = endpoint.rsplit("/", 1)[-1]
            payload = {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "message_id": message_id,
                            "chat_id": CHAT_ID,
                            "sender": {"sender_type": "app", "id": APP_ID},
                            "body": {
                                "content": json.dumps(
                                    _lark_card_v2_user_content(sent_cards[message_id])
                                )
                            },
                        }
                    ]
                },
            }
        elif "messages" in args and "patch" in args:
            message_id = args[args.index("--message-id") + 1]
            update = json.loads(args[args.index("--data") + 1])
            sent_cards[message_id] = json.loads(update["content"])
            payload = {"ok": True, "code": 0}
        elif any("interactive/v1/card/update" in item for item in args):
            update = json.loads(args[args.index("--data") + 1])
            message_id = next(iter(sent_cards))
            sent_cards[message_id] = update["card"]
            payload = {"ok": True, "code": 0}
        else:  # pragma: no cover
            raise AssertionError(args)
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    return run


def _event(proposal: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    action = card["body"]["elements"][3]["columns"][0]["elements"][0]["behaviors"][0][
        "value"
    ]
    return {
        "type": "card.action.trigger",
        "event_id": "evt_operation_card_fixture",
        "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        "operator_id": OPERATOR_ID,
        "message_id": proposal["operation"]["delivery"]["message_id"],
        "chat_id": CHAT_ID,
        "host": "im_message",
        "token": "callback-token-fixture",
        "action_tag": "button",
        "action_value": json.dumps(action),
        "action_name": "",
        "form_value": "",
        "card_content": json.dumps(card),
    }


def test_callback_timestamp_normalizes_milliseconds_and_microseconds() -> None:
    expected = "2023-11-14T22:13:20.123000Z"

    assert goal_channel_operation._callback_timestamp("1700000000123") == expected
    assert goal_channel_operation._callback_timestamp("1700000000123000") == expected

    for invalid in (
        "1700000000",
        "17000000001230",
        "17000000001230000",
        "not-a-timestamp",
    ):
        with pytest.raises(ValueError, match="timestamp is invalid"):
            goal_channel_operation._callback_timestamp(invalid)


def _lark_card_v2_callback_fallback(card: Mapping[str, Any]) -> dict[str, Any]:
    header = card["header"]
    return {
        "title": (f"{header['title']['content']}\n{header['subtitle']['content']}"),
        "elements": [
            [
                {"tag": "img", "image_key": "img_v3_public_fixture"},
                {"tag": "text", "text": "Upgrade the client to view this card"},
                {"tag": "text", "text": ""},
            ]
        ],
    }


def _lark_card_v2_user_content(card: Mapping[str, Any]) -> dict[str, Any]:
    """Approximate the Card 2.0 projection returned by user_card_content."""

    normalized = json.loads(json.dumps(card))
    normalized["config"] = {
        "enable_forward_interaction": False,
        "streaming_mode": False,
        "width_mode": "default",
    }
    normalized["header"].pop("icon")
    sequence = 0

    def visit(value: object) -> None:
        nonlocal sequence
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("tag") in {"column_set", "column", "markdown", "button"}:
            sequence += 1
            value["element_id"] = f"provider_element_{sequence}"
        if value.get("tag") == "column_set":
            value["horizontal_align"] = "left"
        if value.get("tag") == "button":
            value.pop("behaviors", None)
            value.pop("confirm", None)
        for child in value.values():
            visit(child)

    visit(normalized["body"])
    return normalized


def test_card_is_one_bounded_non_forwardable_confirmation_projection(
    tmp_path: Path,
) -> None:
    store, registry, _runtime, _binding, _target = _fixture(tmp_path)
    proposal = _prepare(store, registry)

    card = build_goal_channel_operation_card(proposal)

    assert card["schema"] == "2.0"
    assert card["config"]["enable_forward"] is False
    assert len(card["body"]["elements"]) == 4
    assert card["header"]["icon"]["token"] == "approval_colorful"
    buttons = card["body"]["elements"][3]["columns"]
    confirm_button = buttons[0]["elements"][0]
    assert confirm_button["type"] == "primary_filled"
    assert confirm_button["text"]["content"] == "确认模拟执行"
    assert "confirm" not in confirm_button
    assert buttons[1]["elements"][0]["type"] == "danger"
    assert {
        button["elements"][0]["behaviors"][0]["value"]["decision"] for button in buttons
    } == {"confirm", "reject"}


def test_lark_cards_consume_one_shared_ts_frame_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Mapping[str, Any]]] = []
    frames = iter(
        [
            {
                "schemaVersion": "operation_review_frame_v0",
                "operationId": "operation-1",
                "confirmationDigest": "confirmation-1",
                "kind": "confirmation",
                "simulated": True,
                "content": {
                    "title": "Simulated trade request",
                    "subtitle": "Shared presentation frame",
                    "focus": "BUY 1 SYNTH @ 10 TEST",
                    "fields": [{"label": "Order", "value": "Limit · GTC"}],
                    "warning": "Simulation only.",
                },
            },
            {
                "schemaVersion": "operation_review_frame_v0",
                "operationId": "operation-1",
                "confirmationDigest": "confirmation-1",
                "kind": "result",
                "resultKind": "simulation_completed",
                "summary": "Simulation completed.",
                "content": {
                    "title": "Simulated trade request",
                    "subtitle": "Shared presentation frame",
                    "focus": "BUY 1 SYNTH @ 10 TEST",
                    "fields": [{"label": "Order", "value": "Limit · GTC"}],
                    "warning": "Simulation only.",
                },
            },
        ]
    )

    def compile_frame(method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        calls.append((method, params))
        return {"operationFrame": next(frames)}

    monkeypatch.setattr(goal_channel_operation, "effect_runtime_result", compile_frame)
    proposal = {"proposal_id": "operation-1"}

    confirmation = build_goal_channel_operation_card(proposal)
    result = build_goal_channel_operation_result_card(proposal)

    assert len(calls) == 2
    assert all(
        method == "presentation.action_review_plan.compile"
        and params == {"proposal": proposal}
        for method, params in calls
    )
    assert confirmation["header"]["title"]["content"] == "Simulated trade request"
    assert (
        "confirm"
        not in (confirmation["body"]["elements"][3]["columns"][0]["elements"][0])
    )
    assert result["header"]["text_tag_list"][0]["text"]["content"] == "模拟完成"
    # Lark Card 2.0 rejects `corner_radius` on a column with error 200621,
    # even though some client-side references still list that property.
    for card in (confirmation, result):
        columns = [
            column
            for element in card["body"]["elements"]
            if element.get("tag") == "column_set"
            for column in element["columns"]
        ]
        assert columns
        assert all("corner_radius" not in column for column in columns)


def test_effectful_card_makes_secondary_confirmation_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        goal_channel_operation,
        "effect_runtime_result",
        lambda _method, _params: {
            "operationFrame": {
                "schemaVersion": "operation_review_frame_v0",
                "operationId": "operation-live-1",
                "confirmationDigest": "confirmation-live-1",
                "kind": "confirmation",
                "simulated": False,
                "content": {
                    "title": "Protected operation request",
                    "subtitle": "Exact authorized request",
                    "focus": "One protected effect",
                    "fields": [{"label": "Scope", "value": "Exact request"}],
                    "warning": "Submitting starts the protected operation.",
                },
            }
        },
    )

    card = build_goal_channel_operation_card({"proposal_id": "operation-live-1"})
    button = card["body"]["elements"][3]["columns"][0]["elements"][0]

    assert button["text"]["content"] == "继续并二次确认"
    assert button["confirm"]["title"]["content"] == "确认这个精确请求？"
    assert "第二步确认" in button["confirm"]["text"]["content"]


def test_cli_preparation_previews_without_write_then_persists_canonical_proposal(
    tmp_path: Path,
) -> None:
    store, registry, runtime, _binding, _target = _fixture(tmp_path)
    order = {
        "schema_version": "finance_order_intent_v0",
        "asset": "SYNTH",
        "side": "buy",
        "quantity": "1.00",
        "quantity_unit": "SYNTH",
        "order_type": "limit",
        "limit_price": "10.00",
        "price_unit": "TEST",
        "time_in_force": "GTC",
        "reduce_only": False,
        "maximum_fee": "0.10",
        "fee_unit": "TEST",
    }
    request_path = tmp_path / "operation.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_operation_request_v0",
                "domain": "finance",
                "operation_kind": "finance.order.simulate",
                "operation_schema": "finance_order_intent_v0",
                "payload_ref": "finance-order:cli-fixture",
                "payload": order,
                "payload_digest": _digest(order),
                "projection": {
                    "schema_version": "loopx_operation_projection_v0",
                    "title": "Simulated trade request",
                    "subtitle": "Synthetic fixture",
                    "focus": "BUY 1 SYNTH @ 10 TEST",
                    "fields": [{"label": "Order", "value": "Limit · GTC"}],
                    "warning": "Simulation only.",
                    "simulated": True,
                },
                "destination_account_ref": "account:simulation",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "authorized_principals": [f"lark:{OPERATOR_ID}"],
                "executor": {
                    "extension_id": "loopx-finance-execution",
                    "protocol": "finance_operation_executor_v0",
                    "permission": "finance.operation.simulate",
                    "revision": "simulator-v0",
                },
            }
        ),
        encoding="utf-8",
    )
    preview = _prepare_goal_channel_operation(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        summary="Review one simulated order",
        idempotency_key="cli-operation-fixture",
        request_path=request_path,
        execute=False,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    assert preview["status"] == "preview_ready"
    assert store.list() == []

    applied = _prepare_goal_channel_operation(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        summary="Review one simulated order",
        idempotency_key="cli-operation-fixture",
        request_path=request_path,
        execute=True,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    assert applied["status"] == "awaiting_confirmation"
    assert applied["details"]["durable_proposal_written"] is True
    assert store.load(applied["receipt_id"])["operation"]["lifecycle_state"] == (
        "awaiting_confirmation"
    )


def test_delivery_stops_before_provider_write_when_executor_revision_drifted(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []

    with pytest.raises(ActionConflictError, match="executor revision"):
        deliver_goal_channel_operation_card(
            proposal_id=proposal["proposal_id"],
            action_store_root=store.root,
            runtime_root=runtime,
            binding_path=binding,
            target_path=target,
            execute=True,
            runner=_runner(calls, {}),
            executor_binding_resolver=lambda _parameters, _runtime: {
                "revision": "different-revision"
            },
        )

    assert calls == []


def test_operation_delivery_never_uses_an_unbound_registered_target(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    binding.unlink()
    calls: list[list[str]] = []

    with pytest.raises(ValueError, match="durable binding"):
        deliver_goal_channel_operation_card(
            proposal_id=proposal["proposal_id"],
            action_store_root=store.root,
            runtime_root=runtime,
            binding_path=binding,
            target_path=target,
            execute=True,
            runner=_runner(calls, {}),
            executor_binding_resolver=lambda _parameters, _runtime: {
                "revision": "simulator-v0"
            },
        )

    assert calls == []


def test_delivery_callback_simulation_and_replay_share_one_claim(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    proposal_id = proposal["proposal_id"]
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards)

    delivered = deliver_goal_channel_operation_card(
        proposal_id=proposal_id,
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    assert delivered["status"] == "awaiting_confirmation"
    assert delivered["readback_verified"] is True
    durable = store.load(proposal_id)
    card = sent_cards[durable["operation"]["delivery"]["message_id"]]
    event = _event(durable, card)
    execution_count = 0

    def executor(claimed: dict[str, Any]) -> dict[str, Any]:
        nonlocal execution_count
        execution_count += 1
        operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": operation["operation_id"],
            "payload_digest": operation["payload_digest"],
            "claim_id": operation["claim"]["claim_id"],
            "executor_revision": operation["executor_revision"],
            "summary": "Simulation completed without an external venue write.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    first = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )
    replay = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )

    assert first["outcome"] == replay["outcome"] == "simulated_filled"
    assert first["card_update_verified"] is True
    assert first["external_write_performed"] is True
    assert replay["external_write_performed"] is False
    assert execution_count == 1
    assert store.load(proposal_id)["operation"]["lifecycle_state"] == (
        "outcome_observed"
    )
    assert store.load(proposal_id)["operation"]["result_delivery"]["transport"] == (
        "callback_update"
    )


def test_microsecond_callback_completes_simulation_and_result_delivery(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards)

    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    card = sent_cards[durable["operation"]["delivery"]["message_id"]]
    event = {
        **_event(durable, card),
        "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1_000_000)),
    }

    def executor(claimed: dict[str, Any]) -> dict[str, Any]:
        operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": operation["operation_id"],
            "payload_digest": operation["payload_digest"],
            "claim_id": operation["claim"]["claim_id"],
            "executor_revision": operation["executor_revision"],
            "summary": "Simulation completed without an external venue write.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    receipt = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )

    assert receipt["outcome"] == "simulated_filled"
    assert receipt["card_update_verified"] is True
    operation = store.load(proposal["proposal_id"])["operation"]
    assert operation["lifecycle_state"] == "outcome_observed"
    assert operation["result_delivery"]["transport"] == "callback_update"


def test_card_v2_normalized_readback_and_callback_fallback_complete_simulation(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards, normalized_card_v2_readback=True)

    delivered = deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )

    assert delivered["status"] == "awaiting_confirmation"
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    message_id = durable["operation"]["delivery"]["message_id"]
    card = sent_cards[message_id]
    event = {
        **_event(durable, card),
        "card_content": json.dumps(_lark_card_v2_callback_fallback(card)),
    }
    execution_count = 0

    def executor(claimed: dict[str, Any]) -> dict[str, Any]:
        nonlocal execution_count
        execution_count += 1
        operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": operation["operation_id"],
            "payload_digest": operation["payload_digest"],
            "claim_id": operation["claim"]["claim_id"],
            "executor_revision": operation["executor_revision"],
            "summary": "Simulation completed without an external venue write.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    receipt = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )
    replay = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )

    assert receipt["outcome"] == "simulated_filled"
    assert replay["outcome"] == "simulated_filled"
    assert replay["external_write_performed"] is False
    assert execution_count == 1
    assert receipt["card_update_verified"] is True
    assert (
        store.load(proposal["proposal_id"])["operation"]["result_delivery"]["transport"]
        == "callback_update"
    )


@pytest.mark.parametrize("callback_shape", ["provider_json", "userdsl", "empty"])
def test_provider_normalized_card_v2_callback_and_replay_complete_simulation(
    tmp_path: Path,
    callback_shape: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards, normalized_card_v2_readback=True)

    delivered = deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    assert delivered["status"] == "awaiting_confirmation"
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    card = sent_cards[durable["operation"]["delivery"]["message_id"]]
    assert durable["operation"]["delivery"]["submitted_card"] == card
    callback_content = {
        "provider_json": json.dumps(_lark_card_v2_user_content(card)),
        "userdsl": _normalized_card_v2(card),
        "empty": "",
    }[callback_shape]
    event = {**_event(durable, card), "card_content": callback_content}

    def fail_rebuild(_proposal: Mapping[str, Any]) -> dict[str, Any]:
        raise AssertionError("callback must use the immutable delivery snapshot")

    monkeypatch.setattr(
        goal_channel_operation,
        "_submitted_confirmation_card",
        fail_rebuild,
    )
    execution_count = 0

    def executor(claimed: dict[str, Any]) -> dict[str, Any]:
        nonlocal execution_count
        execution_count += 1
        operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": operation["operation_id"],
            "payload_digest": operation["payload_digest"],
            "claim_id": operation["claim"]["claim_id"],
            "executor_revision": operation["executor_revision"],
            "summary": "Simulation completed without an external venue write.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    first = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )
    replay = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
        executor=executor,
    )

    assert first["outcome"] == replay["outcome"] == "simulated_filled"
    assert first["card_update_verified"] is True
    assert replay["external_write_performed"] is False
    assert execution_count == 1
    if callback_shape == "empty":
        assert any(
            "GET" in call and any("/open-apis/im/v1/messages/" in item for item in call)
            for call in calls
        )


def test_card_v2_callback_fallback_rejects_projection_drift(tmp_path: Path) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards, normalized_card_v2_readback=True)
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    card = sent_cards[durable["operation"]["delivery"]["message_id"]]
    fallback = _lark_card_v2_callback_fallback(card)
    fallback["title"] = "A different operation\nA different request"

    with pytest.raises(ActionConflictError, match="card content drifted"):
        handle_goal_channel_operation_callback(
            {
                **_event(durable, card),
                "card_content": json.dumps(fallback),
            },
            runtime_root=runtime,
            action_store_root=store.root,
            profile_app_id=APP_ID,
            cli_bin="lark-cli",
            profile="operation-bot",
            runner=runner,
            executor=lambda _proposal: {},
        )


def test_concurrent_callback_replay_dispatches_the_claim_once(tmp_path: Path) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    proposal_id = proposal["proposal_id"]
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards)
    deliver_goal_channel_operation_card(
        proposal_id=proposal_id,
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    durable = store.load(proposal_id)
    assert durable is not None
    card = sent_cards[durable["operation"]["delivery"]["message_id"]]
    event = _event(durable, card)
    entered = threading.Event()
    release = threading.Event()
    executions = 0
    receipts: list[dict[str, Any]] = []
    failures: list[BaseException] = []

    def executor(claimed: dict[str, Any]) -> dict[str, Any]:
        nonlocal executions
        executions += 1
        entered.set()
        assert release.wait(timeout=2)
        operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": operation["operation_id"],
            "payload_digest": operation["payload_digest"],
            "claim_id": operation["claim"]["claim_id"],
            "executor_revision": operation["executor_revision"],
            "summary": "Simulation completed once.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def invoke() -> None:
        try:
            receipts.append(
                handle_goal_channel_operation_callback(
                    event,
                    runtime_root=runtime,
                    action_store_root=store.root,
                    profile_app_id=APP_ID,
                    cli_bin="lark-cli",
                    profile="operation-bot",
                    runner=runner,
                    executor=executor,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert failures == []
    assert executions == 1
    assert len(receipts) == 2
    assert all(receipt["outcome"] == "simulated_filled" for receipt in receipts)


def test_callback_does_not_claim_result_delivery_without_native_readback(
    tmp_path: Path,
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards)
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    message_id = durable["operation"]["delivery"]["message_id"]
    event = _event(durable, sent_cards[message_id])

    def stale_update_runner(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, Any]:
        if any("interactive/v1/card/update" in item for item in args):
            return {
                "returncode": 0,
                "stdout": json.dumps({"ok": True, "code": 0}),
                "stderr": "",
            }
        return runner(args, cwd, timeout)

    def executor(claimed: dict[str, Any]) -> dict[str, Any]:
        operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": operation["operation_id"],
            "payload_digest": operation["payload_digest"],
            "claim_id": operation["claim"]["claim_id"],
            "executor_revision": operation["executor_revision"],
            "summary": "Simulation completed once.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    receipt = handle_goal_channel_operation_callback(
        event,
        runtime_root=runtime,
        action_store_root=store.root,
        profile_app_id=APP_ID,
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=stale_update_runner,
        executor=executor,
    )

    assert receipt["ok"] is False
    assert receipt["card_update_verified"] is False
    assert receipt["external_write_performed"] is True
    assert receipt["status"] == "result_delivery_pending"
    assert store.load(proposal["proposal_id"])["operation"]["outcome"]["outcome"] == (
        "simulated_filled"
    )

    recovered = recover_goal_channel_operation_results(
        action_store_root=store.root,
        profile_app_id=APP_ID,
        allowed_chat_ids={CHAT_ID},
        cli_bin="lark-cli",
        profile="operation-bot",
        runner=runner,
    )

    assert recovered == {"attempted": 1, "delivered": 1, "failed": 0}
    readback = ChatActionStore(store.root).load(proposal["proposal_id"])
    assert readback["operation"]["result_delivery"]["transport"] == "message_patch"


def test_restart_recovers_only_claimed_non_effectful_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=_runner(calls, sent_cards),
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    durable = store.load(proposal["proposal_id"])
    assert durable is not None
    operation = durable["operation"]
    store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation={
            "provider": "lark",
            "event_id": "evt_restart_simulation_fixture",
            "principal": f"lark:{OPERATOR_ID}",
            "message_id": operation["delivery"]["message_id"],
            "chat_id": CHAT_ID,
            "app_id": APP_ID,
            "surface_kind": "im_message",
            "interaction_kind": "card_button",
            "confirmation_digest": operation["confirmation_digest"],
            "card_digest": operation["delivery"]["card_digest"],
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    def recovered_executor(claimed: dict[str, Any], *, runtime_root: Path):
        assert runtime_root == runtime
        claimed_operation = claimed["operation"]
        return {
            "schema_version": "loopx_operation_outcome_v0",
            "outcome": "simulated_filled",
            "projection_verified": True,
            "operation_id": claimed_operation["operation_id"],
            "payload_digest": claimed_operation["payload_digest"],
            "claim_id": claimed_operation["claim"]["claim_id"],
            "executor_revision": claimed_operation["executor_revision"],
            "summary": "Recovered simulation completed without an external write.",
            "simulation": True,
            "external_write_performed": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr(
        goal_channel_operation, "_execute_claimed_operation", recovered_executor
    )

    first = recover_goal_channel_simulation_claims(
        action_store_root=store.root,
        runtime_root=runtime,
    )
    replay = recover_goal_channel_simulation_claims(
        action_store_root=store.root,
        runtime_root=runtime,
    )

    assert first == {"attempted": 1, "observed": 1, "failed": 0}
    assert replay == {"attempted": 0, "observed": 0, "failed": 0}
    readback = ChatActionStore(store.root).load(proposal["proposal_id"])
    assert readback is not None
    assert readback["operation"]["lifecycle_state"] == "outcome_observed"
    assert readback["operation"]["outcome"]["external_write_performed"] is False


def test_forwarded_or_unauthorized_card_cannot_claim(tmp_path: Path) -> None:
    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []
    sent_cards: dict[str, dict[str, Any]] = {}
    runner = _runner(calls, sent_cards)
    deliver_goal_channel_operation_card(
        proposal_id=proposal["proposal_id"],
        action_store_root=store.root,
        runtime_root=runtime,
        binding_path=binding,
        target_path=target,
        execute=True,
        runner=runner,
        executor_binding_resolver=lambda _parameters, _runtime: {
            "revision": "simulator-v0"
        },
    )
    durable = store.load(proposal["proposal_id"])
    card = sent_cards[durable["operation"]["delivery"]["message_id"]]
    event = _event(durable, card)

    with pytest.raises(ActionConflictError, match="delivered request"):
        handle_goal_channel_operation_callback(
            {**event, "message_id": "om_forwarded_fixture"},
            runtime_root=runtime,
            action_store_root=store.root,
            profile_app_id=APP_ID,
            cli_bin="lark-cli",
            profile="operation-bot",
            runner=runner,
            executor=lambda _proposal: {},
        )
    with pytest.raises(ActionConflictError, match="not authorized"):
        handle_goal_channel_operation_callback(
            {**event, "operator_id": "ou_untrusted_fixture"},
            runtime_root=runtime,
            action_store_root=store.root,
            profile_app_id=APP_ID,
            cli_bin="lark-cli",
            profile="operation-bot",
            runner=runner,
            executor=lambda _proposal: {},
        )


def test_prepare_rejects_stale_executor_revision_before_any_persistence(
    tmp_path: Path,
) -> None:
    """A stale revision is blocked in preview and execute without any write."""

    store, registry, runtime, _binding, _target = _fixture(tmp_path)
    request_path = tmp_path / "operation.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_operation_request_v0",
                "domain": "finance",
                "operation_kind": "finance.order.simulate",
                "operation_schema": "finance_order_intent_v0",
                "payload_ref": "finance-order:stale-fixture",
                "payload": {"asset": "SYNTH"},
                "payload_digest": _digest({"asset": "SYNTH"}),
                "projection": {
                    "schema_version": "loopx_operation_projection_v0",
                    "title": "Simulated trade request",
                    "subtitle": "Synthetic fixture",
                    "focus": "BUY 1 SYNTH @ 10 TEST",
                    "fields": [{"label": "Order", "value": "Limit · GTC"}],
                    "warning": "Simulation only.",
                    "simulated": True,
                },
                "destination_account_ref": "account:simulation",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "authorized_principals": [f"lark:{OPERATOR_ID}"],
                "executor": {
                    "extension_id": "loopx-finance-execution",
                    "protocol": "finance_operation_executor_v0",
                    "permission": "finance.operation.simulate",
                    "revision": "requested-v1",
                },
            }
        ),
        encoding="utf-8",
    )

    for execute in (False, True):
        with pytest.raises(OperationExecutorDriftError) as exc_info:
            _prepare_goal_channel_operation(
                registry_path=registry,
                runtime_root=runtime,
                goal_id=GOAL_ID,
                agent_id=AGENT_ID,
                summary="Review one simulated order",
                idempotency_key=f"stale-operation-{execute}",
                request_path=request_path,
                execute=execute,
                executor_binding_resolver=lambda _parameters, _runtime: {
                    "revision": "active-v9"
                },
            )
        assert exc_info.value.blocker == "executor_revision_drift"
        assert exc_info.value.failure_stage == "resolve_executor_binding"
        assert exc_info.value.details == {
            "requested_executor_revision": "requested-v1",
            "active_executor_revision": "active-v9",
        }
        assert exc_info.value.external_write_performed is False

    durable = json.loads((store.root / "actions.json").read_text())
    assert durable["proposals"] == {}
    assert durable["idempotency"] == {}


def test_prepare_rejects_unresolvable_executor_without_private_leak(
    tmp_path: Path,
) -> None:
    """Resolution failures project a typed blocker without private text."""

    store, registry, runtime, _binding, _target = _fixture(tmp_path)
    request_path = tmp_path / "operation.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_operation_request_v0",
                "domain": "finance",
                "operation_kind": "finance.order.simulate",
                "operation_schema": "finance_order_intent_v0",
                "payload_ref": "finance-order:unavailable-fixture",
                "payload": {"asset": "SYNTH"},
                "payload_digest": _digest({"asset": "SYNTH"}),
                "projection": {
                    "schema_version": "loopx_operation_projection_v0",
                    "title": "Simulated trade request",
                    "subtitle": "Synthetic fixture",
                    "focus": "BUY 1 SYNTH @ 10 TEST",
                    "fields": [{"label": "Order", "value": "Limit · GTC"}],
                    "warning": "Simulation only.",
                    "simulated": True,
                },
                "destination_account_ref": "account:simulation",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "authorized_principals": [f"lark:{OPERATOR_ID}"],
                "executor": {
                    "extension_id": "loopx-finance-execution",
                    "protocol": "finance_operation_executor_v0",
                    "permission": "finance.operation.simulate",
                    "revision": "simulator-v0",
                },
            }
        ),
        encoding="utf-8",
    )

    def broken_resolver(_parameters: object, _runtime: object) -> object:
        raise ValueError("private resolver detail: /home/operator/secret-path")

    with pytest.raises(GoalChannelDeliveryStageError) as exc_info:
        _prepare_goal_channel_operation(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            summary="Review one simulated order",
            idempotency_key="unavailable-operation",
            request_path=request_path,
            execute=True,
            executor_binding_resolver=broken_resolver,
        )

    assert str(exc_info.value) == "operation executor binding is unavailable"
    assert exc_info.value.blocker == "executor_unavailable"
    assert "secret-path" not in str(exc_info.value)
    durable = json.loads((store.root / "actions.json").read_text())
    assert durable["proposals"] == {}
    assert durable["idempotency"] == {}


def test_delivery_projects_typed_stage_blockers(tmp_path: Path) -> None:
    """Stage failures keep their typed blocker and stage at the deliver seam."""

    def _failed_delivery(
        case: str,
        fail_args: tuple[str, ...],
        *,
        outcome: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> tuple[GoalChannelDeliveryStageError, list[list[str]]]:
        case_root = tmp_path / case
        case_root.mkdir()
        store, registry, runtime, binding, target = _fixture(case_root)
        proposal = _prepare(store, registry)
        calls: list[list[str]] = []
        base = _runner(calls, {})

        def runner(
            args: list[str], cwd: Path | None, timeout: float | None
        ) -> dict[str, Any]:
            if all(fragment in args for fragment in fail_args):
                calls.append(list(args))
                if raises is not None:
                    raise raises
                if outcome is not None:
                    return dict(outcome)
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "provider rejected",
                }
            return base(args, cwd, timeout)

        with pytest.raises(GoalChannelDeliveryStageError) as exc_info:
            deliver_goal_channel_operation_card(
                proposal_id=proposal["proposal_id"],
                action_store_root=store.root,
                runtime_root=runtime,
                binding_path=binding,
                target_path=target,
                execute=True,
                runner=runner,
                executor_binding_resolver=lambda _parameters, _runtime: {
                    "revision": "simulator-v0"
                },
            )
        return exc_info.value, calls

    blocked, identity_calls = _failed_delivery("identity", ("auth", "status"))
    assert blocked.blocker == "sender_identity_unverified"
    assert blocked.failure_stage == "verify_sender_identity"
    assert blocked.external_write_performed is False
    assert not any("+messages-send" in call for call in identity_calls)

    blocked, dedupe_calls = _failed_delivery("dedupe", ("+chat-messages-list",))
    assert blocked.blocker == "dedupe_history_read_failed"
    assert blocked.failure_stage == "read_dedupe_history"
    assert blocked.external_write_performed is False
    assert not any("+messages-send" in call for call in dedupe_calls)

    blocked, send_calls = _failed_delivery("send", ("+messages-send",))
    assert blocked.blocker == "provider_send_rejected"
    assert blocked.failure_stage == "send_operation_card"
    assert blocked.external_write_performed is False

    # A provider answer is the only thing that can be projected as a verdict.
    # Without one the card may already be live, so the send stage must report an
    # unknown outcome rather than a clean no-write.
    for case, outcome, raises in (
        ("send-no-body", {"returncode": 1, "stdout": "", "stderr": ""}, None),
        (
            "send-timeout",
            None,
            subprocess.TimeoutExpired(cmd="lark-cli", timeout=30),
        ),
    ):
        blocked, send_calls = _failed_delivery(
            case, ("+messages-send",), outcome=outcome, raises=raises
        )
        assert blocked.blocker == "delivery_outcome_unknown", (case, blocked)
        assert blocked.failure_stage == "send_operation_card", (case, blocked)
        assert blocked.external_write_performed is None, (case, blocked)
        assert any("+messages-send" in call for call in send_calls), case

    # A command that never started cannot have written anything, and says so
    # with its own blocker instead of being reported as a provider rejection.
    blocked, send_calls = _failed_delivery(
        "send-unavailable",
        ("+messages-send",),
        raises=FileNotFoundError("lark-cli"),
    )
    assert blocked.blocker == "provider_unavailable", blocked
    assert blocked.failure_stage == "send_operation_card", blocked
    assert blocked.external_write_performed is False, blocked


def test_receipt_treats_post_send_failure_as_unknown_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the provider write must not project a clean receipt."""

    store, registry, runtime, binding, target = _fixture(tmp_path)
    proposal = _prepare(store, registry)
    calls: list[list[str]] = []

    def failing_record(
        self: ChatActionStore, *args: object, **kwargs: object
    ) -> object:
        raise OSError("synthetic receipt write failure")

    monkeypatch.setattr(ChatActionStore, "record_operation_delivery", failing_record)

    with pytest.raises(GoalChannelDeliveryStageError) as exc_info:
        deliver_goal_channel_operation_card(
            proposal_id=proposal["proposal_id"],
            action_store_root=store.root,
            runtime_root=runtime,
            binding_path=binding,
            target_path=target,
            execute=True,
            runner=_runner(calls, {}),
            executor_binding_resolver=lambda _parameters, _runtime: {
                "revision": "simulator-v0"
            },
        )

    # The readback already proved the card is live, so this stage keeps its own
    # blocker instead of diluting the "provider outcome unknown" signal.
    assert exc_info.value.blocker == "delivery_receipt_write_failed"
    assert exc_info.value.failure_stage == "record_delivery_receipt"
    assert exc_info.value.external_write_performed is None
    assert any("+messages-send" in call for call in calls)
