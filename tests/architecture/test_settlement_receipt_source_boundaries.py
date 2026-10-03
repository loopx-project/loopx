"""Executed sources, wire inputs and compatibility are different evidence.

The Turn reducer creates step/failure values from journal/provider facts; the
shared envelope also accepts them as external inputs. Receipt phases instead
come from readback facts. These witnesses do not enroll any of these four
vocabularies in F1/F2 or establish whole-program producer completeness.
"""

from __future__ import annotations

import json
import subprocess
from itertools import product
from pathlib import Path

import pytest

from loopx.control_plane.effect_program import (
    SettlementIdentity,
    decode_settlement_result_payload,
    receipt_bound_monitor_phase,
    receipt_bound_replay_phase,
    receipt_bound_terminal_phase,
)
from loopx.control_plane.effect_runtime import (
    EffectRuntimeRejected,
    effect_runtime_result,
)
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.control_plane.turn_driver.settlement import execute_turn_driver_settlement
from loopx.control_plane.work_items.work_lane import (
    preserve_heartbeat_receipt_bound_work_lane,
)

ROOT = Path(__file__).resolve().parents[2]
PHASES = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
]
COMMITTED = {"ok": True, "appended": True}
IDENTITY_INPUT = dict(
    goal_id="witness-goal",
    agent_id="witness-agent",
    todo_id="todo_witness",
    turn_instance_id="witness-turn",
)


@pytest.fixture(scope="module")
def identity():
    return SettlementIdentity(**IDENTITY_INPUT).as_dict()


def _direct(operation, payload):
    completed = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-strip-types",
            str(ROOT / "scripts/settlement_receipt_source_witness.mts"),
        ],
        input=json.dumps({"operation": operation, "input": payload}),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _turn(identity, **overrides):
    return {
        "schema_version": "loopx_turn_settlement_transaction_v1",
        "transaction_plan": {"settlement_plan": {"identity": identity}},
        "transaction_phases": PHASES,
        "completed_phases": PHASES,
        "committed_effect_id": identity["effect_id"],
        "writeback_payload": COMMITTED,
        "quota_spend_payload": COMMITTED,
        "terminal_closeout_required": False,
        "terminal_closeout_payload": None,
        "failed_provider_attempt": None,
        "effect_attempts": {},
        "provider_observations": {},
        **overrides,
    }


# These are causal inputs, not a loop over declared enum members.
@pytest.mark.parametrize(
    "overrides,expected",
    [
        (
            {"transaction_plan": {"settlement_plan": {"identity": {}}}},
            "invalid_identity",
        ),
        ({"completed_phases": PHASES[:2]}, "receipt_missing"),
        ({"committed_effect_id": "another-effect"}, "identity_mismatch"),
        (
            {
                "completed_phases": PHASES[:3],
                "writeback_payload": None,
                "quota_spend_payload": None,
                "failed_provider_attempt": {
                    "step_kind": "durable_writeback",
                    "payload": {"ok": False, "reason": "refused"},
                },
            },
            "writeback_rejected",
        ),
        (
            {
                "completed_phases": PHASES[:4],
                "quota_spend_payload": None,
                "failed_provider_attempt": {
                    "step_kind": "quota_spend",
                    "payload": {"ok": False, "reason": "refused"},
                },
            },
            "quota_spend_rejected",
        ),
        (
            {
                "completed_phases": PHASES[:4],
                "quota_spend_payload": None,
                "failed_provider_attempt": {
                    "step_kind": "quota_spend",
                    "payload": {"ok": False, "reason": "budget exhausted"},
                },
            },
            "budget_rejected",
        ),
        (
            {
                "terminal_closeout_required": True,
                "turn_result_kind": "validated_progress",
            },
            "terminal_closeout_rejected",
        ),
    ],
)
def test_turn_owner_produces_failures_and_bridge_decodes_them(
    identity, overrides, expected
):
    request = _turn(identity, **overrides)
    direct = _direct("turn", request)
    bridged = effect_runtime_result("turn.settlement.reduce", request)
    assert bridged == direct
    assert direct["decision"] == "failed"
    assert direct["provider_effects"] == []
    _, _, failure = decode_settlement_result_payload(bridged["result"])
    assert failure.kind.value == expected


def test_prepared_unknown_is_not_retry_or_success(identity):
    request = _turn(
        identity,
        completed_phases=PHASES[:3],
        writeback_payload=None,
        quota_spend_payload=None,
        effect_attempts={
            "durable_writeback": {
                "status": "prepared",
                "effect_ref": identity["effect_id"] + "#durable_writeback",
            }
        },
        provider_observations={
            "durable_writeback": {"kind": "unknown", "reason": "unavailable"}
        },
    )
    direct = _direct("turn", request)
    assert effect_runtime_result("turn.settlement.reduce", request) == direct
    assert direct["decision"] == "failed"
    assert direct["provider_effects"] == []
    assert direct["result"]["failure"]["kind"] == "effect_outcome_unknown"


@pytest.mark.parametrize("terminal", [False, True])
def test_committed_replay_constructs_receipts_without_executing_again(
    identity, terminal
):
    overrides = dict(terminal_closeout_required=terminal)
    if terminal:
        overrides.update(
            turn_result_kind="validated_completion",
            terminal_closeout_payload={
                **COMMITTED,
                "completion": {
                    "todo_id": identity["todo_id"],
                    "continuation": "no_followup",
                },
            },
        )
    request = _turn(identity, **overrides)
    direct = _direct("turn", request)
    assert effect_runtime_result("turn.settlement.reduce", request) == direct
    expected = ["validation", "durable_writeback", "quota_spend"]
    if terminal:
        expected.append("terminal_closeout")
    assert direct["decision"] == "complete"
    assert direct["provider_effects"] == []
    assert [r["step_kind"] for r in direct["result"]["receipts"]] == expected
    assert {r["effect_id"] for r in direct["result"]["receipts"]} == {
        identity["effect_id"]
    }

    def forbidden(*args):
        pytest.fail("settled replay must not dispatch or checkpoint providers")

    result = execute_turn_driver_settlement(
        request["transaction_plan"],
        transaction_phases=PHASES,
        completed_phases=PHASES,
        committed_effect_id=identity["effect_id"],
        writeback_payload=COMMITTED,
        quota_spend_payload=COMMITTED,
        writeback=forbidden,
        spend=forbidden,
        checkpoint=forbidden,
        terminal_closeout=forbidden,
        terminal_checkpoint=forbidden,
        terminal_closeout_payload=request["terminal_closeout_payload"],
        terminal_closeout_required=terminal,
        turn_result_kind=request.get("turn_result_kind"),
    )
    assert result.failure is None
    assert [r.step_kind.value for r in result.receipts] == expected


# Explicit injected envelopes prove decoder admission only, never production.
@pytest.mark.parametrize(
    "kind",
    [
        "invalid_identity",
        "receipt_missing",
        "identity_mismatch",
        "writeback_missing",
        "writeback_rejected",
        "quota_spend_rejected",
        "terminal_closeout_rejected",
        "cancelled",
        "permission_denied",
        "budget_rejected",
        "effect_outcome_unknown",
    ],
)
def test_external_failure_envelope_survives_ts_and_python_decoders(kind):
    envelope = {
        "value": None,
        "receipts": [],
        "failure": {
            "kind": kind,
            "step_kind": "validation",
            "reason": "external refusal",
        },
    }
    result = effect_runtime_result("settlement.bind_gate", {"result": envelope})
    assert result == {"execute": False, "result": envelope}
    _, _, failure = decode_settlement_result_payload(result["result"])
    assert failure.kind.value == kind


@pytest.mark.parametrize(
    "step", ["validation", "durable_writeback", "quota_spend", "terminal_closeout"]
)
def test_external_receipt_step_survives_both_decoders(identity, step):
    envelope = {
        "value": {},
        "failure": None,
        "receipts": [
            {
                "step_kind": step,
                "status": "committed",
                "effect_id": identity["effect_id"],
            }
        ],
    }
    result = effect_runtime_result("settlement.bind_gate", {"result": envelope})
    assert result == {"execute": True, "result": envelope}
    _, receipts, _ = decode_settlement_result_payload(result["result"])
    assert receipts[0].step_kind.value == step


@pytest.mark.parametrize("slot", ["receipt_step", "failure_step", "failure_kind"])
@pytest.mark.parametrize("bad", ["unknown", None, 3])
def test_external_unknowns_are_rejected_by_each_decoder(slot, bad):
    envelope = {"value": None, "receipts": [], "failure": None}
    if slot == "receipt_step":
        envelope["receipts"] = [
            {"step_kind": bad, "status": "committed", "effect_id": "effect"}
        ]
    else:
        envelope["failure"] = {
            "kind": "cancelled",
            "step_kind": "validation",
            "reason": "refused",
        }
        envelope["failure"]["kind" if slot == "failure_kind" else "step_kind"] = bad
    with pytest.raises(EffectRuntimeRejected, match="settlement|must be"):
        effect_runtime_result("settlement.bind_gate", {"result": envelope})
    with pytest.raises(RuntimeError, match="shape mismatch"):
        decode_settlement_result_payload(envelope)


def _readback_fixture(
    root,
    identity,
    *,
    completion=False,
    writeback=False,
    spend=False,
    monitor_effect=None,
    material=False,
    guard_effect=None,
):
    goal = root / "goals" / identity["goal_id"]
    (goal / "runs").mkdir(parents=True)
    common = {"goal_id": identity["goal_id"], "agent_id": identity["agent_id"]}

    def event(kind, **details):
        return {
            **common,
            "schema_version": "loopx_rollout_event_v0",
            "event_id": kind,
            "event_kind": kind,
            "run_id": identity["turn_instance_id"],
            "details": {"settlement_effect_id": identity["effect_id"], **details},
        }

    events = [
        event(
            "quota_should_run",
            todo_id=identity["todo_id"],
            settlement_effect_id=guard_effect or identity["effect_id"],
        )
    ]
    runs = []
    for present, event_kind, classification in [
        (writeback, "refresh_state", "state_refreshed"),
        (spend, "quota_spend", "quota_slot_spent"),
    ]:
        if present:
            events.append(event(event_kind))
            runs.append(
                {
                    **identity,
                    "classification": classification,
                    "settlement_identity": identity,
                    "delivery_outcome": "outcome_progress",
                }
            )
    if completion:
        events.append(event("todo_complete", no_followup=True))
    # A row without the exact native commit must not close a monitor Turn.
    runs.append(
        {
            **identity,
            "classification": "quota_monitor_poll",
            "material_change": material,
            "quota_monitor_poll_commit": {"effect_id": monitor_effect},
        }
    )
    for path, rows in [
        (goal / "rollout-event-log.jsonl", events),
        (goal / "runs/index.jsonl", runs),
    ]:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return {
        "schema_version": "loopx_quota_settlement_readback_request_v0",
        "runtime_root": str(root),
        **IDENTITY_INPUT,
        "replan_obligation_id": None,
        "infer_turn_instance_id": False,
        "allow_unbound_binding": False,
    }


@pytest.mark.parametrize(
    "completion,writeback,spend,expected",
    [
        (False, False, False, "open"),
        (False, True, True, "open"),
        (True, False, False, "settlement_pending"),
        (True, True, False, "settlement_pending"),
        (True, True, True, "settled"),
    ],
)
def test_replay_phase_comes_from_real_receipt_readback(
    tmp_path, identity, completion, writeback, spend, expected
):
    request = _readback_fixture(
        tmp_path, identity, completion=completion, writeback=writeback, spend=spend
    )
    direct = _direct("readback", request)
    assert effect_runtime_result("quota.settlement.read", request) == direct
    readback = read_heartbeat_settlement(tmp_path, **IDENTITY_INPUT)
    assert readback.replay_phase.value == direct["replay_phase"] == expected
    assert read_heartbeat_settlement(tmp_path, **IDENTITY_INPUT) == readback
    if not writeback:
        assert readback.writeback.failure.kind.value == "writeback_missing"


@pytest.mark.parametrize(
    "suffix,expected",
    [
        (None, "poll_due"),
        (":todo:another", "poll_due"),
        (":todo:todo_witness", "settled"),
        ("", "settled"),
    ],
)
@pytest.mark.parametrize("material", [False, True])
def test_monitor_requires_exact_commit_and_accepts_legacy_commit_id(
    tmp_path, identity, suffix, expected, material
):
    effect = (
        None
        if suffix is None
        else "quota-monitor-poll:witness-goal:witness-agent:witness-turn" + suffix
    )
    request = _readback_fixture(
        tmp_path, identity, monitor_effect=effect, material=material
    )
    direct = _direct("readback", request)
    readback = read_heartbeat_settlement(tmp_path, **IDENTITY_INPUT)
    assert readback.monitor_phase.value == direct["monitor_phase"] == expected
    assert readback.spend.failure is not None  # monitor closeout requires no spend


def test_readback_rejects_other_effect_before_projecting_phases(tmp_path, identity):
    request = _readback_fixture(
        tmp_path,
        identity,
        completion=True,
        writeback=True,
        spend=True,
        guard_effect="other-effect",
    )
    direct = _direct("readback", request)
    readback = read_heartbeat_settlement(tmp_path, **IDENTITY_INPUT)
    assert (
        readback.identity.failure.kind.value
        == direct["identity"]["result"]["failure"]["kind"]
        == "identity_mismatch"
    )
    assert readback.monitor_phase is None and readback.replay_phase is None


def test_monitor_builder_never_emits_compatibility_pending():
    for poll, material, writeback, spend in product([False, True], repeat=4):
        phase = receipt_bound_monitor_phase(
            poll_present=poll,
            material_change=material,
            durable_writeback_present=writeback,
            quota_spend_present=spend,
        )
        assert phase.value == ("settled" if poll else "poll_due")


@pytest.mark.parametrize(
    "phase,obligation",
    [
        ("poll_due", "attempt_due_monitor"),
        ("settlement_pending", "settle_receipt_bound_monitor"),
        ("settled", "finish_settled_receipt_bound_monitor_turn"),
    ],
)
def test_monitor_consumer_retains_compatibility_pending(phase, obligation):
    projected = preserve_heartbeat_receipt_bound_work_lane(
        {},
        selected_todo={
            "todo_id": "todo_witness",
            "selection_binding": "heartbeat_receipt",
            "task_class": "continuous_monitor",
            "receipt_bound_monitor_phase": phase,
        },
    )
    assert projected["obligation"] == obligation


def test_monitor_consumer_rejects_unknown_phase():
    with pytest.raises(ValueError, match="explicit monitor phase"):
        preserve_heartbeat_receipt_bound_work_lane(
            {},
            selected_todo={
                "todo_id": "todo_witness",
                "selection_binding": "heartbeat_receipt",
                "task_class": "continuous_monitor",
                "receipt_bound_monitor_phase": "unknown",
            },
        )


@pytest.mark.parametrize(
    "binding,completion,writeback,spend,expected",
    [
        ("todo", False, True, True, "open"),
        ("unbound", False, True, True, "open"),
        ("autonomous_replan", False, False, True, "open"),
        ("autonomous_replan", False, True, False, "settlement_pending"),
        ("autonomous_replan", False, True, True, "settled"),
    ],
)
def test_replay_builder_obeys_binding_not_only_receipt_presence(
    binding, completion, writeback, spend, expected
):
    assert (
        receipt_bound_replay_phase(
            binding_kind=binding,
            completion_receipt_present=completion,
            durable_writeback_present=writeback,
            quota_spend_present=spend,
        ).value
        == expected
    )


def test_replay_bridge_rejects_unknown_binding():
    with pytest.raises(EffectRuntimeRejected, match="unsupported settlement binding"):
        receipt_bound_replay_phase(
            binding_kind="unknown",
            completion_receipt_present=True,
            durable_writeback_present=True,
            quota_spend_present=True,
        )


@pytest.mark.parametrize(
    "completion,writeback,spend,expected",
    [
        (False, True, True, "open"),
        (True, False, False, "settlement_pending"),
        (True, True, True, "settled"),
    ],
)
def test_terminal_compatibility_adapter_keeps_replay_semantics(
    completion, writeback, spend, expected
):
    assert (
        receipt_bound_terminal_phase(
            terminal_closeout_present=completion,
            durable_writeback_present=writeback,
            quota_spend_present=spend,
        ).value
        == expected
    )
