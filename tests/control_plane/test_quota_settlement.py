from __future__ import annotations

import json
import shlex
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from loopx.control_plane import effect_program as core_effect_program
from loopx.control_plane.effect_program import (
    SettlementFailureKind,
    SettlementIdentity,
    SettlementReceipt,
    SettlementResult,
    SettlementStepKind,
)
from loopx.control_plane.quota import effect_program as quota_effect_program
from loopx.control_plane.quota import settlement as quota_settlement
from loopx.control_plane.quota.heartbeat_receipt import (
    ensure_turn_heartbeat_settlement_receipt,
    find_heartbeat_receipt,
    heartbeat_receipt_settlement_replan_obligation_id,
    heartbeat_receipt_settlement_todo_id,
)
from loopx.control_plane.quota.settlement import (
    build_codex_app_settlement_plan,
    read_heartbeat_settlement,
    settlement_step_command,
)
from loopx.control_plane.quota.settlement_cli import (
    quota_rollout_replan_obligation_id,
    quota_rollout_todo_id,
)
from loopx.control_plane.quota.error_codes import (
    HeartbeatReceiptIdentityConflictError,
)
from loopx.control_plane.quota.turn_envelope import quota_action_signature_document
from loopx.control_plane.scheduler.execution_context import (
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.work_items.interaction_contract import (
    build_interaction_contract,
    interaction_next_cli_actions,
)
from loopx.cli_commands.todo import _completion_settlement_error
from loopx.rollout_event_log import rollout_event_log_path

GOAL_ID = "settlement-goal"
AGENT_ID = "codex-settlement"
TODO_ID = "todo_settlement"
TURN_ID = "turn-settlement-1"


@pytest.mark.parametrize("replan", [False, True])
def test_command_plan_supplies_actor_from_identity(replan):
    plan = quota_effect_program.build_turn_scoped_cli_settlement_plan(
        goal_id=GOAL_ID, agent_id=AGENT_ID, command_prefix="loopx",
        todo_id=None if replan else TODO_ID,
        replan_obligation_id="replan-0000000000000001" if replan else None,
        turn_instance_id="turn with spaces", scoped_cli_args="", lifecycle_actor_args="",
    )
    for step in plan.as_dict()["ordered_steps"]:
        if command := step.get("command_template"):
            argv = shlex.split(command)
            assert argv.count("--agent-id") == 1
            assert argv[argv.index("--agent-id") + 1] == AGENT_ID
            assert argv[argv.index("--turn-instance-id") + 1] == "turn with spaces"


@pytest.mark.parametrize("arguments", ["--agent-id other", "--agent-id=other", "--agent-id",
                                      f"--agent-id {AGENT_ID} --agent-id {AGENT_ID}"])
@pytest.mark.parametrize("field", ["scoped_cli_args", "lifecycle_actor_args"])
def test_command_plan_rejects_ambiguous_or_conflicting_actor(arguments, field):
    with pytest.raises(ValueError, match="actor must match"):
        quota_effect_program.build_turn_scoped_cli_settlement_plan(
            goal_id=GOAL_ID, agent_id=AGENT_ID, command_prefix="loopx",
            todo_id=TODO_ID, replan_obligation_id=None, turn_instance_id=TURN_ID,
            **{"scoped_cli_args": "", "lifecycle_actor_args": "", field: arguments},
        )


@pytest.mark.parametrize("arguments", [f"--agent-id {AGENT_ID}", f" --agent-id={AGENT_ID}"])
def test_command_plan_retains_one_matching_actor(arguments):
    plan = quota_effect_program.build_turn_scoped_cli_settlement_plan(
        goal_id=GOAL_ID, agent_id=AGENT_ID, command_prefix="loopx",
        todo_id=TODO_ID, replan_obligation_id=None, turn_instance_id=TURN_ID,
        scoped_cli_args=arguments, lifecycle_actor_args=arguments,
    )
    argv = shlex.split(settlement_step_command(plan.as_dict(), SettlementStepKind.QUOTA_SPEND))
    assert argv[argv.index("--turn-instance-id") + 1] == TURN_ID
    assert sum(arg == "--agent-id" or arg.startswith("--agent-id=") for arg in argv) == 1


def _receipt(step: SettlementStepKind, marker: str) -> SettlementReceipt:
    return SettlementReceipt(
        step_kind=step,
        status="committed",
        effect_id="effect-1",
        source_ref=marker,
    )


def test_quota_reexports_the_core_settlement_algebra() -> None:
    assert (
        quota_effect_program.SettlementIdentity
        is core_effect_program.SettlementIdentity
    )
    assert quota_effect_program.SettlementPlan is core_effect_program.SettlementPlan
    assert quota_effect_program.SettlementResult is core_effect_program.SettlementResult


def test_autonomous_replan_replay_phase_settles_from_refresh_and_spend() -> None:
    assert (
        core_effect_program.receipt_bound_replay_phase(
            binding_kind=core_effect_program.SettlementBindingKind.AUTONOMOUS_REPLAN,
            completion_receipt_present=False,
            durable_writeback_present=True,
            quota_spend_present=True,
        )
        is core_effect_program.ReceiptBoundReplayPhase.SETTLED
    )
    assert (
        core_effect_program.receipt_bound_replay_phase(
            completion_receipt_present=False,
            durable_writeback_present=True,
            quota_spend_present=True,
        )
        is core_effect_program.ReceiptBoundReplayPhase.OPEN
    )


def test_settlement_identity_rejects_dual_binding_before_projection() -> None:
    with pytest.raises(ValueError, match="cannot bind both"):
        SettlementIdentity(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            todo_id=TODO_ID,
            turn_instance_id=TURN_ID,
            replan_obligation_id="replan-0000000000000001",
        )


def test_replan_bound_heartbeat_receipt_is_not_projected_as_a_todo() -> None:
    obligation_id = "replan-0000000000000001"
    event = {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "run_id": TURN_ID,
        "details": {"replan_obligation_id": obligation_id},
    }

    assert heartbeat_receipt_settlement_todo_id(event) is None
    assert heartbeat_receipt_settlement_replan_obligation_id(event) == obligation_id


def test_selected_todo_suppresses_stale_replan_packet_binding() -> None:
    payload = {
        "selected_todo": {"todo_id": TODO_ID},
        "replan_action_packet": {
            "obligation_id": "replan-0000000000000001",
        },
    }
    args = SimpleNamespace(todo_id=None, replan_obligation_id=None)

    assert quota_rollout_todo_id(payload, args) == TODO_ID
    assert quota_rollout_replan_obligation_id(payload, args) is None


def test_explicit_replan_binding_survives_newly_selected_todo() -> None:
    obligation_id = "replan-0000000000000001"
    payload = {"selected_todo": {"todo_id": TODO_ID}}
    args = SimpleNamespace(
        todo_id=None,
        replan_obligation_id=obligation_id,
    )

    assert quota_rollout_todo_id(payload, args) is None
    assert quota_rollout_replan_obligation_id(payload, args) == obligation_id


def test_rollout_projection_rejects_corrupted_dual_bound_plan() -> None:
    payload = {
        "interaction_contract": {
            "cli_channel": {
                "settlement_plan": {
                    "identity": {
                        "todo_id": TODO_ID,
                        "replan_obligation_id": "replan-0000000000000001",
                    }
                }
            }
        }
    }
    args = SimpleNamespace(todo_id=None, replan_obligation_id=None)

    with pytest.raises(ValueError, match="cannot bind both"):
        quota_rollout_todo_id(payload, args)


def _append_guard_receipt(
    runtime_root: Path,
    *,
    todo_id: str = TODO_ID,
    replan_obligation_id: str | None = None,
    effect_id: str | None = None,
) -> None:
    path = rollout_event_log_path(runtime_root, GOAL_ID)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_rollout_event_v0",
                "event_id": "event-guard",
                "event_kind": "quota_should_run",
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "run_id": TURN_ID,
                "details": {
                    "todo_id": todo_id,
                    **(
                        {"replan_obligation_id": replan_obligation_id}
                        if replan_obligation_id is not None
                        else {}
                    ),
                    **(
                        {"settlement_effect_id": effect_id}
                        if effect_id is not None
                        else {}
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _append_run_index_record(runtime_root: Path, record: dict) -> None:
    path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_turn_guard_upgrades_matching_legacy_receipt_to_explicit_empty_scope(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)

    ensure_turn_heartbeat_settlement_receipt(
        runtime_root,
        identity,
        semantic_replan_guard_scoped=False,
        semantic_replan_obligation_id=None,
    )
    upgraded = ensure_turn_heartbeat_settlement_receipt(
        runtime_root,
        identity,
        semantic_replan_guard_scoped=True,
        semantic_replan_obligation_id=None,
    )
    replayed = ensure_turn_heartbeat_settlement_receipt(
        runtime_root,
        identity,
        semantic_replan_guard_scoped=True,
        semantic_replan_obligation_id=None,
    )

    assert upgraded["details"]["semantic_replan_obligation_id"] is None
    assert replayed == upgraded
    events = [
        json.loads(line)
        for line in rollout_event_log_path(runtime_root, GOAL_ID)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(events) == 2
    assert events[1]["causality"] == {
        "caused_by": events[0]["event_id"],
        "source_event_id": events[0]["event_id"],
    }

    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback is not None
    assert readback.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": None,
    }


@pytest.mark.parametrize(
    "torn_record",
    [
        b'{"schema_version":"loopx_rollout_event_v0"',
        b'{"summary":"' + "雪".encode()[:2],
    ],
    ids=["ascii", "mid-utf8"],
)
def test_turn_guard_remains_readable_after_a_torn_rollout_tail(
    tmp_path: Path,
    torn_record: bytes,
) -> None:
    runtime_root = tmp_path / "runtime"
    event_path = rollout_event_log_path(runtime_root, GOAL_ID)
    event_path.parent.mkdir(parents=True)
    event_path.write_bytes(torn_record)
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)

    written = ensure_turn_heartbeat_settlement_receipt(
        runtime_root,
        identity,
        semantic_replan_guard_scoped=False,
        semantic_replan_obligation_id=None,
    )

    readback = find_heartbeat_receipt(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback is not None
    assert readback["event_id"] == written["event_id"]


def test_turn_guard_refuses_to_change_an_existing_semantic_selection(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    obligation_id = "replan-0000000000000001"
    ensure_turn_heartbeat_settlement_receipt(
        runtime_root,
        identity,
        semantic_replan_guard_scoped=True,
        semantic_replan_obligation_id=obligation_id,
    )

    with pytest.raises(
        HeartbeatReceiptIdentityConflictError,
        match="another semantic replan guard",
    ):
        ensure_turn_heartbeat_settlement_receipt(
            runtime_root,
            identity,
            semantic_replan_guard_scoped=True,
            semantic_replan_obligation_id=None,
        )


def test_quota_settlement_readback_returns_the_complete_typed_chain(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    _append_guard_receipt(runtime_root, effect_id=identity.effect_id)
    event_path = rollout_event_log_path(runtime_root, GOAL_ID)
    with event_path.open("a", encoding="utf-8") as handle:
        for event_id, event_kind in (
            ("event-writeback", "refresh_state"),
            ("event-spend", "quota_spend"),
        ):
            handle.write(
                json.dumps(
                    {
                        "schema_version": "loopx_rollout_event_v0",
                        "event_id": event_id,
                        "event_kind": event_kind,
                        "goal_id": GOAL_ID,
                        "agent_id": AGENT_ID,
                        "run_id": TURN_ID,
                        "details": {"settlement_effect_id": identity.effect_id},
                    }
                )
                + "\n"
            )
    _append_run_index_record(
        runtime_root,
        {
            "classification": "state_refreshed",
            "delivery_outcome": "outcome_progress",
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "todo_id": TODO_ID,
            "turn_instance_id": TURN_ID,
        },
    )
    _append_run_index_record(
        runtime_root,
        {
            "classification": "quota_slot_spent",
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "todo_id": TODO_ID,
            "turn_instance_id": TURN_ID,
        },
    )

    with patch.object(
        quota_settlement,
        "effect_runtime_result",
        wraps=quota_settlement.effect_runtime_result,
    ) as runtime_result:
        readback = read_heartbeat_settlement(
            runtime_root,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            todo_id=TODO_ID,
            turn_instance_id=TURN_ID,
        )

    assert readback is not None
    assert runtime_result.call_count == 1
    assert readback.identity.value == identity
    assert readback.settlement.failure is None
    assert [receipt.step_kind for receipt in readback.settlement.receipts] == [
        SettlementStepKind.VALIDATION,
        SettlementStepKind.DURABLE_WRITEBACK,
        SettlementStepKind.QUOTA_SPEND,
    ]
    assert readback.spend_run is not None


def test_only_terminal_closeout_requires_the_complete_settlement_chain(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    _append_guard_receipt(runtime_root, effect_id=identity.effect_id)

    incomplete = read_heartbeat_settlement(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert incomplete is not None
    error = _completion_settlement_error(
        incomplete,
        no_follow_up=True,
    )
    assert error is not None
    assert error.startswith(
        "terminal no-follow-up closeout requires matching writeback and "
        "quota spend receipts:"
    )

    settled = replace(
        incomplete,
        settlement=SettlementResult(
            value={},
            receipts=(
                _receipt(SettlementStepKind.VALIDATION, "validation"),
                _receipt(SettlementStepKind.DURABLE_WRITEBACK, "writeback"),
                _receipt(SettlementStepKind.QUOTA_SPEND, "spend"),
            ),
            failure=None,
        ),
    )
    assert (
        _completion_settlement_error(
            settled,
            no_follow_up=True,
        )
        is None
    )
    assert (
        _completion_settlement_error(
            incomplete,
            no_follow_up=False,
        )
        is None
    )


@pytest.mark.parametrize(
    ("guard_state", "failure_kind"),
    [
        ("missing", SettlementFailureKind.RECEIPT_MISSING),
        ("mismatched", SettlementFailureKind.IDENTITY_MISMATCH),
    ],
)
def test_python_settlement_readback_fails_closed_for_invalid_or_missing_guard(
    tmp_path: Path,
    guard_state: str,
    failure_kind: SettlementFailureKind,
) -> None:
    runtime_root = tmp_path / "runtime"
    if guard_state == "mismatched":
        _append_guard_receipt(runtime_root, todo_id="todo_other")
    for classification in (
        "quota_monitor_poll",
        "state_refreshed",
        "quota_slot_spent",
    ):
        _append_run_index_record(
            runtime_root,
            {
                "classification": classification,
                "material_change": True,
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "todo_id": TODO_ID,
                "turn_instance_id": TURN_ID,
            },
        )

    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    assert readback.identity.failure is not None
    assert readback.identity.failure.kind is failure_kind
    assert readback.monitor_phase is None
    assert readback.replay_phase is None


def test_terminal_closeout_receipt_rejects_ordinary_completion_event(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    path = rollout_event_log_path(runtime_root, GOAL_ID)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_rollout_event_v0",
                "event_id": "event-ordinary-completion",
                "event_kind": "todo_complete",
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "run_id": TURN_ID,
                "details": {
                    "settlement_effect_id": identity.effect_id,
                    "no_followup": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    result = readback.terminal_closeout
    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.RECEIPT_MISSING
    assert result.failure.step_kind is SettlementStepKind.TERMINAL_CLOSEOUT


def test_settlement_result_satisfies_left_and_right_identity() -> None:
    def writeback(value: int) -> SettlementResult[int]:
        return SettlementResult.pure(
            value + 1,
            receipts=(_receipt(SettlementStepKind.DURABLE_WRITEBACK, "writeback"),),
        )

    assert SettlementResult.pure(2).bind(writeback) == writeback(2)
    result = writeback(2)
    assert result.bind(SettlementResult.pure) == result


def test_settlement_result_satisfies_associativity_and_preserves_order() -> None:
    def writeback(value: int) -> SettlementResult[int]:
        return SettlementResult.pure(
            value + 1,
            receipts=(_receipt(SettlementStepKind.DURABLE_WRITEBACK, "writeback"),),
        )

    def spend(value: int) -> SettlementResult[str]:
        return SettlementResult.pure(
            str(value),
            receipts=(_receipt(SettlementStepKind.QUOTA_SPEND, "spend"),),
        )

    initial = SettlementResult.pure(1)
    left = initial.bind(writeback).bind(spend)
    right = initial.bind(lambda value: writeback(value).bind(spend))

    assert left == right
    assert [receipt.step_kind for receipt in left.receipts] == [
        SettlementStepKind.DURABLE_WRITEBACK,
        SettlementStepKind.QUOTA_SPEND,
    ]
    assert (
        initial.bind(spend).bind(lambda value: writeback(int(value))).receipts
        != left.receipts
    )


@pytest.mark.parametrize(
    "failure_kind",
    [
        SettlementFailureKind.CANCELLED,
        SettlementFailureKind.PERMISSION_DENIED,
        SettlementFailureKind.BUDGET_REJECTED,
    ],
)
def test_settlement_result_short_circuits_without_erasing_failure(
    failure_kind: SettlementFailureKind,
) -> None:
    failed: SettlementResult[int] = SettlementResult.failed(
        kind=failure_kind,
        step_kind=SettlementStepKind.DURABLE_WRITEBACK,
        reason="writeback denied",
        receipts=(_receipt(SettlementStepKind.VALIDATION, "validation"),),
    )
    called = False

    def spend(_value: int) -> SettlementResult[str]:
        nonlocal called
        called = True
        return SettlementResult.pure("spent")

    result = failed.bind(spend)

    assert called is False
    assert result.failure == failed.failure
    assert result.failure is not None
    assert result.failure.kind is failure_kind
    assert result.receipts == failed.receipts


def test_settlement_result_bind_rejects_corrupted_receipts() -> None:
    def writeback(value: int) -> SettlementResult[int]:
        return SettlementResult.pure(value + 1)

    with patch.object(core_effect_program, "effect_runtime_result") as runtime_result:
        runtime_result.side_effect = [
            {"execute": True},
            {
                "value": {
                    "completed_phases": [],
                    "writeback": None,
                    "quota_spend": None,
                },
                "receipts": ["corrupted-receipt"],
                "failure": None,
            },
        ]

        with pytest.raises(RuntimeError, match="receipts shape mismatch"):
            SettlementResult.pure(2).bind(writeback)


def test_codex_app_plan_projects_one_identity_across_settlement_steps() -> None:
    plan = build_codex_app_settlement_plan(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        scoped_cli_args=f" --agent-id {AGENT_ID}",
        lifecycle_actor_args=f" --agent-id {AGENT_ID}",
    ).as_dict()

    assert plan["identity"]["todo_id"] == TODO_ID
    assert plan["identity"]["turn_instance_id"] == "${LOOPX_TURN:?}"
    assert [step["kind"] for step in plan["ordered_steps"]] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    for kind in (
        SettlementStepKind.DURABLE_WRITEBACK,
        SettlementStepKind.QUOTA_SPEND,
        SettlementStepKind.TERMINAL_CLOSEOUT,
    ):
        command = settlement_step_command(plan, kind)
        assert command is not None
        assert f"--todo-id {TODO_ID}" in command
        assert '--turn-instance-id "${LOOPX_TURN:?}"' in command
    assert plan["host_handoff"]["inside_agent_settlement"] is False


def test_cli_validation_projects_ordinary_completion_not_terminal_intent() -> None:
    plan = build_codex_app_settlement_plan(
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
        scoped_cli_args="", lifecycle_actor_args="", turn_instance_id_ref=TURN_ID,
    ).as_dict()
    validation = plan["ordered_steps"][0]
    assert validation["command_condition"] == "todo_deliverable_complete"
    command = settlement_step_command(plan, SettlementStepKind.VALIDATION)
    assert command is not None
    argv = shlex.split(command)
    assert "--no-follow-up" not in argv
    assert "--next-agent-todo" not in argv
    assert argv[argv.index("--todo-id") + 1] == TODO_ID
    assert argv[argv.index("--agent-id") + 1] == AGENT_ID
    assert argv[argv.index("--turn-instance-id") + 1] == TURN_ID


def test_in_flight_validation_never_projects_a_completion_command() -> None:
    plan = build_codex_app_settlement_plan(
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
        scoped_cli_args="", lifecycle_actor_args="", turn_instance_id_ref=TURN_ID,
        delivery_boundary="in_flight_continuation",
    ).as_dict()
    assert settlement_step_command(plan, SettlementStepKind.VALIDATION) is None
    assert "command_condition" not in plan["ordered_steps"][0]


def test_native_plan_rerender_reuses_one_runtime_projection_without_shared_mutability() -> None:
    with patch.object(quota_effect_program, "effect_runtime_result",
                      wraps=quota_effect_program.effect_runtime_result) as runtime:
        plan = build_codex_app_settlement_plan(
            goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            scoped_cli_args="", lifecycle_actor_args="", turn_instance_id_ref=TURN_ID,
        )
        first = plan.as_dict()
        first["ordered_steps"][0]["command_template"] = "modified by a consumer"
        assert plan.as_dict()["ordered_steps"][0]["command_template"] != "modified by a consumer"
        assert runtime.call_count == 1


@pytest.mark.parametrize(
    ("todo_id", "replan_obligation_id"),
    [
        (None, None),
        (TODO_ID, "replan-0000000000000001"),
    ],
)
def test_codex_app_plan_rejects_ambiguous_settlement_binding(
    todo_id: str | None,
    replan_obligation_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="requires exactly one"):
        build_codex_app_settlement_plan(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            todo_id=todo_id,
            replan_obligation_id=replan_obligation_id,
            scoped_cli_args=f" --agent-id {AGENT_ID}",
            lifecycle_actor_args=f" --agent-id {AGENT_ID}",
        )


@pytest.mark.parametrize(
    "profile",
    (
        SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT,
        SchedulerRuntimeProfile.TRAE_APP,
    ),
)
def test_standard_app_actions_use_typed_settlement_before_turn_driver(
    profile: SchedulerRuntimeProfile,
) -> None:
    todo_id = "todo_123456789abc"
    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "selected_todo": {"todo_id": todo_id},
        },
        mode="bounded_delivery",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            profile
        ),
    )

    assert len(actions) == 2
    assert actions[0].startswith("loopx refresh-state")
    assert actions[1].startswith("loopx quota spend-slot")
    for command in actions:
        assert f"--todo-id {todo_id}" in command
        assert '--turn-instance-id "${LOOPX_TURN:?}"' in command


def test_codex_app_actions_preserve_a_concrete_admitted_turn_identity() -> None:
    todo_id = "todo_concrete_turn"
    turn_instance_id = "guided-start:concrete-turn"

    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "selected_todo": {"todo_id": todo_id},
        },
        mode="bounded_delivery",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
        ),
        turn_instance_id=turn_instance_id,
    )

    assert len(actions) == 2
    for command in actions:
        assert f"--turn-instance-id {turn_instance_id}" in command
        assert "${LOOPX_TURN:?}" not in command


@pytest.mark.parametrize(
    "profile",
    (
        SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL,
        SchedulerRuntimeProfile.CODEX_CLI_VISIBLE,
    ),
)
def test_unbound_native_goal_actions_require_host_identity_before_settlement(
    profile: SchedulerRuntimeProfile,
) -> None:
    todo_id = "todo_visible_goal"
    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "selected_todo": {"todo_id": todo_id},
        },
        mode="bounded_delivery",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            profile
        ),
    )

    assert len(actions) == 1
    assert "quota should-run" in actions[0]
    assert "--turn-instance-id" in actions[0]
    assert "--begin-turn" not in actions[0]
    assert "spend-slot" not in actions[0]
    assert "refresh-state" not in actions[0]


def test_unbound_codex_app_ssh_goal_requires_a_guided_turn_before_delivery() -> None:
    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "selected_todo": {"todo_id": TODO_ID},
        },
        mode="bounded_delivery",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE
        ),
    )

    assert len(actions) == 1
    assert actions[0].startswith("loopx --format json quota should-run")
    assert "--runtime-profile codex_app_ssh_goal" in actions[0]
    assert actions[0].endswith("--begin-turn")
    assert "spend-slot" not in actions[0]


@pytest.mark.parametrize(
    "profile", (
        SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE,
        SchedulerRuntimeProfile.CODEX_CLI_VISIBLE,
        SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL,
    ),
)
def test_unbound_native_goal_requires_identity_before_replan(profile) -> None:
    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "execution_obligation": {
                "kind": "autonomous_replan_required",
                "must_attempt_work": True,
                "delivery_allowed": True,
            },
            "replan_action_packet": {
                "schema_version": "replan_action_packet_v0",
                "obligation_id": "replan-0000000000000001",
                "required_outcome": "semantic_delta",
                "uncovered_frontier": {"required_any_of": ["new_surface"]},
                "writeback_contract": {},
                "allowed_terminal": ["blocked"],
            },
        },
        mode="autonomous_replan",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            profile
        ),
    )

    assert len(actions) == 1
    assert actions[0].startswith("loopx --format json quota should-run")
    assert f"--runtime-profile {profile.value}" in actions[0]
    if profile is SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE:
        assert actions[0].endswith("--begin-turn")
    else:
        assert "--turn-instance-id" in actions[0]
        assert "--begin-turn" not in actions[0]
    assert "refresh-state" not in actions[0]
    assert "spend-slot" not in actions[0]


@pytest.mark.parametrize(
    "profile", (
        SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE,
        SchedulerRuntimeProfile.CODEX_CLI_VISIBLE,
        SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL,
    ),
)
def test_turn_bound_native_goal_preserves_visible_goal_settlement(profile) -> None:
    turn_instance_id = "guided-start:native-visible-goal"

    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "selected_todo": {"todo_id": TODO_ID},
        },
        mode="bounded_delivery",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            profile
        ),
        turn_instance_id=turn_instance_id,
    )

    assert len(actions) == 2
    assert actions[0].startswith("loopx refresh-state")
    assert actions[1].startswith("loopx quota spend-slot")
    assert "--source visible-goal" in actions[1]
    for command in actions:
        assert f"--todo-id {TODO_ID}" in command
        assert f"--turn-instance-id {turn_instance_id}" in command


def test_claude_visible_goal_reenters_before_exposing_bound_settlement() -> None:
    payload = {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "selected_todo": {"todo_id": TODO_ID},
    }
    context = scheduler_execution_context_for_runtime_profile(
        SchedulerRuntimeProfile.CLAUDE_CODE_VISIBLE
    )

    unbound = interaction_next_cli_actions(
        payload,
        mode="bounded_delivery",
        scheduler_execution_context=context,
    )

    assert len(unbound) == 1
    assert unbound[0].startswith("loopx --format json quota should-run")
    assert "--runtime-profile claude_code" in unbound[0]
    assert "--turn-instance-id" in unbound[0]
    assert "refresh-state" not in unbound[0]
    assert "spend-slot" not in unbound[0]

    turn_instance_id = "claude-visible-goal-turn-1"
    bound = interaction_next_cli_actions(
        payload,
        mode="bounded_delivery",
        scheduler_execution_context=context,
        turn_instance_id=turn_instance_id,
    )

    assert len(bound) == 2
    assert bound[0].startswith("loopx refresh-state")
    assert bound[1].startswith("loopx quota spend-slot")
    assert "--source visible-goal" in bound[1]
    for command in bound:
        assert f"--todo-id {TODO_ID}" in command
        assert f"--turn-instance-id {turn_instance_id}" in command


def test_codex_app_external_observation_settles_only_substantive_writeback() -> None:
    todo_id = "todo_external_observation"
    actions = interaction_next_cli_actions(
        {
            "goal_id": GOAL_ID,
            "agent_identity": {"agent_id": AGENT_ID},
            "selected_todo": {"todo_id": todo_id},
        },
        mode="external_evidence_observation",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
        ),
    )

    assert len(actions) == 3
    assert actions[0].startswith("read approved")
    assert actions[1].startswith("on a substantive transition or blocker only:")
    assert "--delivery-outcome <outcome>" in actions[1]
    assert f"--todo-id {todo_id}" in actions[1]
    assert '--turn-instance-id "${LOOPX_TURN:?}"' in actions[1]
    assert actions[2].startswith("after that accountable writeback receipt only:")
    assert f"--todo-id {todo_id}" in actions[2]
    assert '--turn-instance-id "${LOOPX_TURN:?}"' in actions[2]
    assert "otherwise do not spend for unchanged observation" in actions[2]


def _generic_cli_contract_payload(*, replan: bool = False) -> dict:
    payload = {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "should_run": True,
        "normal_delivery_allowed": True,
        "execution_obligation": {
            "must_attempt_work": True,
            "delivery_allowed": True,
            **({"kind": "autonomous_replan_required"} if replan else {}),
        },
    }
    if replan:
        payload["replan_action_packet"] = {
            "obligation_id": "replan-0000000000000001"
        }
    else:
        payload["selected_todo"] = {"todo_id": TODO_ID}
    return payload


@pytest.mark.parametrize("replan", [False, True])
def test_turn_scoped_generic_cli_contract_uses_exact_settlement_identity(
    replan: bool,
) -> None:
    contract = build_interaction_contract(
        _generic_cli_contract_payload(replan=replan),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP
        ),
        turn_instance_id=TURN_ID,
    )

    plan = contract["cli_channel"]["settlement_plan"]
    identity = plan["identity"]
    assert identity["turn_instance_id"] == TURN_ID
    assert identity["effect_id"].endswith(f":{TURN_ID}")
    binding = (
        "--replan-obligation-id replan-0000000000000001"
        if replan
        else f"--todo-id {TODO_ID}"
    )
    for step in plan["ordered_steps"]:
        command = step.get("command_template")
        if command:
            assert binding in command
            assert f"--turn-instance-id {TURN_ID}" in command
            assert "${LOOPX_TURN:?}" not in command


def test_todo_bound_replan_projects_one_settlement_binding_and_full_chain() -> None:
    obligation_id = "replan-0000000000000001"
    payload = {
        **_generic_cli_contract_payload(),
        "execution_obligation": {
            "kind": "autonomous_replan_required",
            "must_attempt_work": True,
            "delivery_allowed": True,
        },
        "replan_action_packet": {
            "schema_version": "replan_action_packet_v0",
            "obligation_id": obligation_id,
            "required_outcome": "semantic_delta",
            "uncovered_frontier": {"required_any_of": ["new_surface"]},
            "writeback_contract": {},
            "allowed_terminal": ["blocked"],
        },
    }

    contract = build_interaction_contract(
        payload,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP
        ),
        turn_instance_id=TURN_ID,
    )

    channel = contract["cli_channel"]
    assert channel["replan_settlement_contract"] == {
        "schema_version": "replan_settlement_contract_v0",
        "single_binding_required": True,
        "settlement_binding": {
            "kind": "todo",
            "id": TODO_ID,
            "cli_argument": "--todo-id",
        },
        "semantic_obligation": {
            "kind": "autonomous_replan",
            "id": obligation_id,
            "settlement_bound": False,
            "discharge": "todo_bound_writeback",
        },
    }
    identity = channel["settlement_plan"]["identity"]
    assert identity["todo_id"] == TODO_ID
    assert "replan_obligation_id" not in identity
    actions = channel["next_cli_actions"]
    assert len(actions) == 2
    assert "--delivery-outcome outcome_progress" in actions[0]
    assert "spend-slot" in actions[1]
    assert all(f"--todo-id {TODO_ID}" in action for action in actions)
    assert all("--replan-obligation-id" not in action for action in actions)
    signature = quota_action_signature_document(
        {**payload, "interaction_contract": contract}
    )
    assert signature["writeback"]["replan_settlement_contract"] == channel[
        "replan_settlement_contract"
    ]


def test_unscoped_replan_keeps_compact_guidance_without_settlement_contract() -> None:
    payload = {
        **_generic_cli_contract_payload(),
        "execution_obligation": {
            "kind": "autonomous_replan_required",
            "must_attempt_work": True,
            "delivery_allowed": True,
        },
        "replan_action_packet": {
            "schema_version": "replan_action_packet_v0",
            "obligation_id": "replan-0000000000000001",
            "required_outcome": "semantic_delta",
            "uncovered_frontier": {"required_any_of": ["new_surface"]},
            "writeback_contract": {},
            "allowed_terminal": ["blocked"],
        },
    }

    contract = build_interaction_contract(
        payload,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP
        ),
    )

    channel = contract["cli_channel"]
    assert "settlement_plan" not in channel
    assert "replan_settlement_contract" not in channel
    assert len(channel["next_cli_actions"]) == 1
    assert "--delivery-outcome" not in channel["next_cli_actions"][0]
    assert "spend-slot" not in channel["next_cli_actions"][0]


def test_generic_cli_without_turn_identity_keeps_legacy_unbound_actions() -> None:
    contract = build_interaction_contract(
        _generic_cli_contract_payload(),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP
        ),
    )

    channel = contract["cli_channel"]
    assert "settlement_plan" not in channel
    assert all("--turn-instance-id" not in action for action in channel["next_cli_actions"])


def test_interaction_contract_cli_actions_keep_runtime_root() -> None:
    runtime_root = "/tmp/loopx-runtime-root-fixture"
    command_prefix = f"loopx --runtime-root {runtime_root}"

    contract = build_interaction_contract(
        _generic_cli_contract_payload(),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP
        ),
        turn_instance_id=TURN_ID,
        runtime_root=runtime_root,
    )

    channel = contract["cli_channel"]
    assert channel["next_cli_actions"][0].startswith(
        f"{command_prefix} refresh-state "
    )
    assert channel["next_cli_actions"][1].startswith(
        f"{command_prefix} quota spend-slot "
    )
    plan = channel["settlement_plan"]
    for step in plan["ordered_steps"]:
        command = step.get("command_template")
        if command:
            assert command.startswith(command_prefix)


def test_guard_receipt_resolves_stable_settlement_identity(tmp_path: Path) -> None:
    _append_guard_receipt(tmp_path)

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    result = readback.identity
    assert result.failure is None
    assert result.value is not None
    assert result.value.effect_id == (f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{TURN_ID}")
    assert result.receipts[0].step_kind is SettlementStepKind.VALIDATION


def test_guard_receipt_rejects_different_todo(tmp_path: Path) -> None:
    _append_guard_receipt(tmp_path, todo_id="todo_original")

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id="todo_successor",
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    result = readback.identity
    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH
    assert result.failure.step_kind is SettlementStepKind.VALIDATION


def test_guard_receipt_rejects_different_effect_identity(tmp_path: Path) -> None:
    _append_guard_receipt(tmp_path, effect_id="different-effect")

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    result = readback.identity
    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH
    assert "different-effect" in result.failure.reason


def test_guard_receipt_rejects_corrupted_dual_binding(tmp_path: Path) -> None:
    _append_guard_receipt(
        tmp_path,
        replan_obligation_id="replan-0000000000000001",
        effect_id=f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{TURN_ID}",
    )

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    result = readback.identity
    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH
    assert "conflicting Todo and autonomous replan bindings" in result.failure.reason


def test_guard_receipt_returns_typed_failure_for_effect_without_todo(
    tmp_path: Path,
) -> None:
    _append_guard_receipt(tmp_path, todo_id="", effect_id="effect-without-todo")

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )

    assert readback is not None
    result = readback.identity
    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH
    assert "effect identity without a Todo" in result.failure.reason


@pytest.mark.parametrize(
    "missing_field",
    [
        "run_agent_id",
        "settlement_identity",
        "identity_schema_version",
        "run_todo_id",
        "run_turn_instance_id",
    ],
)
def test_unbound_visible_goal_recovery_requires_fully_typed_same_agent_run(
    tmp_path: Path,
    missing_field: str,
) -> None:
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    _append_guard_receipt(tmp_path, effect_id=identity.effect_id)
    record = {
        "classification": "state_refreshed",
        "delivery_outcome": "outcome_progress",
        "agent_id": AGENT_ID,
        "todo_id": TODO_ID,
        "turn_instance_id": TURN_ID,
        "settlement_identity": identity.as_dict(),
    }
    if missing_field == "identity_schema_version":
        record["settlement_identity"].pop("schema_version")
    else:
        record.pop(missing_field.removeprefix("run_"), None)
    _append_run_index_record(tmp_path, record)

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=None,
        turn_instance_id=None,
        infer_turn_instance_id=True,
        allow_unbound_binding=True,
    )

    assert readback is not None
    result = readback.identity
    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH


def test_typed_material_poll_is_recovered_not_shadowed(tmp_path: Path) -> None:
    _append_guard_receipt(tmp_path)
    _append_run_index_record(
        tmp_path,
        {
            "classification": "quota_slot_spent",
            "agent_id": AGENT_ID,
            "todo_id": TODO_ID,
            "turn_instance_id": "turn-settlement-stale",
        },
    )
    _append_run_index_record(
        tmp_path,
        {
            "classification": "quota_monitor_poll",
            "material_change": True,
            "delivery_outcome": "outcome_progress",
            "agent_id": AGENT_ID,
            "todo_id": TODO_ID,
            "turn_instance_id": TURN_ID,
        },
    )

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=None,
        infer_turn_instance_id=True,
    )

    assert readback is not None
    result = readback.identity
    assert result.value is not None
    assert result.value.turn_instance_id == TURN_ID


def test_unknown_non_neutral_record_fails_closed(tmp_path: Path) -> None:
    _append_guard_receipt(tmp_path)
    _append_run_index_record(
        tmp_path,
        {
            "classification": "quota_slot_spent",
            "agent_id": AGENT_ID,
            "todo_id": TODO_ID,
            "turn_instance_id": TURN_ID,
        },
    )
    _append_run_index_record(
        tmp_path,
        {
            "classification": "quota_monitor_poll",
            "material_change": True,
            "agent_id": AGENT_ID,
        },
    )

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=None,
        infer_turn_instance_id=True,
    )

    assert readback is None
