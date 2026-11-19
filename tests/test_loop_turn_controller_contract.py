"""Independent semantic cells for the complete controller, not its route slice.

The hand-written expected rows come from continuation obligations. They are
neither read from the shipped contract nor obtained by executing the controller.
The existing suite owns qualification fixtures and rejection counterexamples.
"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import loop_controller as controller
from loopx.control_plane.turn_driver.driver import LoopXTurnRoute
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.turn_contract_generated import (
    TURN_CONTROLLER_CONTRACT as CONTRACT, project_turn_route,
)
from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
from loopx.control_plane.turn_driver.transaction import LoopXTurnResultKind
from test_loop_turn_loop_controller import (
    _ALL_PHASES,
    _assert_markers,
    _budget,
    _envelope,
    _execution,
    _validated_receipt,
)


@pytest.mark.parametrize("field", ["goal_id", "agent_id", "todo_id"])
@pytest.mark.parametrize("empty", [[], {}, None, False, 0, ""])
def test_execution_receipt_rejects_empty_lineage_before_qualification(field, empty):
    execution = _execution()
    execution["receipt"]["lineage"][field] = empty
    with pytest.raises(ValueError, match="missing goal/agent/todo lineage"):
        controller.ValidatedTurnReceipt.from_execution(execution)


@pytest.mark.parametrize("field", ["goal_id", "agent_id", "todo_id"])
def test_execution_receipt_retains_nonempty_lineage(field):
    execution = _execution()
    expected = deepcopy(execution["receipt"]["lineage"])
    expected[field] = "identity-中文"
    execution["receipt"]["lineage"] = expected
    assert controller.ValidatedTurnReceipt.from_execution(execution).lineage == expected


@pytest.mark.parametrize("source,signature", [(1.0, "1.0"), (1e-7, "1e-07"),
    (True, "True"), ("sha256:fixture", "sha256:fixture")])
def test_historical_scalar_route_reaches_public_plan_and_controller(source, signature):
    envelope = _envelope(should_run=True)
    envelope["action_signature"].update(source_hash=source, envelope_hash=signature)
    plan = build_loopx_turn_plan(envelope, host="codex-cli", execution_mode="interactive-visible")
    assert plan["route"]["kind"] == "ready_for_host"
    assert plan["route"]["would_invoke_host"] is True
    decision = controller.decide_loop_disposition(turn_receipt=None, quota_decision=envelope)
    assert decision["disposition"] == "run_now"
    envelope["action_signature"]["envelope_hash"] = "different-signature"
    rejected = build_loopx_turn_plan(envelope, host="codex-cli", execution_mode="interactive-visible")
    assert rejected["route"]["kind"] == "contract_error"
    assert rejected["route"]["would_invoke_host"] is False
    with pytest.raises(ValueError, match="signature hashes"):
        controller.decide_loop_disposition(turn_receipt=None, quota_decision=envelope)


# ready / capability / repair / replan / user / quiet wait / blocked
_ROUTES = [
    "ready_for_host",
    "capability_action_required",
    "repair_required",
    "replan_required",
    "user_action_required",
    "wait",
    "blocked",
]
_PROJECTED = [
    "run_now",
    "capability_action_required",
    "repair",
    "replan",
    "user_action_required",
    "wait",
    "wait",
]
_REPAIR = [
    "repair",
    "repair",
    "repair",
    "repair",
    "user_action_required",
    "repair",
    "repair",
]
_ROWS = [
    ("absent", _PROJECTED),
    ("validated_progress", _PROJECTED),
    (
        "progress_exhausted",
        [
            "replan",
            "capability_action_required",
            "replan",
            "replan",
            "user_action_required",
            "replan",
            "replan",
        ],
    ),
    (
        "progress_unbounded",
        [
            "reject",
            "capability_action_required",
            "reject",
            "reject",
            "user_action_required",
            "reject",
            "reject",
        ],
    ),
    ("completion_successor", _PROJECTED),
    ("completion_active_goal", _PROJECTED),
    (
        "completion_no_followup",
        [
            "reject",
            "capability_action_required",
            "reject",
            "reject",
            "reject",
            "reject",
            "reject",
        ],
    ),
    ("repair_required", _REPAIR),
    (
        "replan_required",
        [
            "replan",
            "replan",
            "replan",
            "replan",
            "user_action_required",
            "replan",
            "replan",
        ],
    ),
    ("user_action_required", ["user_action_required"] * 7),
    ("wait", ["wait", "wait", "wait", "wait", "user_action_required", "wait", "wait"]),
    (
        "iteration_failed",
        ["stop", "stop", "stop", "stop", "user_action_required", "stop", "stop"],
    ),
    ("host_failure", _REPAIR),
    (
        "host_retry",
        ["wait", "wait", "repair", "replan", "user_action_required", "wait", "wait"],
    ),
    (
        "host_exhausted",
        [
            "repair",
            "repair",
            "repair",
            "replan",
            "user_action_required",
            "repair",
            "repair",
        ],
    ),
    (
        "host_nonretryable",
        [
            "repair",
            "repair",
            "repair",
            "replan",
            "user_action_required",
            "repair",
            "repair",
        ],
    ),
    ("validation_failed", _REPAIR),
    # Qualified task replan overrides automatic work routes, but never the
    # existing owner-action gate. Host-contract failures retain generic repair.
    ("validation_replan", ["replan", "replan", "replan", "replan", "user_action_required", "replan", "replan"]),
    ("validation_repair", _REPAIR),
    ("validation_host_contract", _REPAIR),
    ("writeback_failed", _REPAIR),
    ("quota_spend_failed", _REPAIR),
    ("terminal_closeout_failed", _REPAIR),
]


def _cell(row, route):
    kwargs = {}
    budget = _budget()
    todo = "todo-1"
    kind = row
    if row.startswith("completion_"):
        kind = "validated_completion"
        kwargs = {
            "continuation": row.removeprefix("completion_"),
            "successor_todo_ids": ["todo-next"],
        }
        todo = "todo-next"
    elif row in {"progress_exhausted", "progress_unbounded"}:
        kind = "validated_progress"
        budget = (
            _budget(max_turns=1, completed_turns=1)
            if row == "progress_exhausted"
            else None
        )
    elif row in {"host_retry", "host_exhausted", "host_nonretryable"}:
        kind = "host_failure"
        kwargs = {
            "host_failure": build_host_failure_record(
                "auth_failed" if row == "host_nonretryable" else "provider_capacity",
                attempt=3 if row == "host_exhausted" else 1,
            )
        }
    elif row == "terminal_closeout_failed":
        kwargs = {
            "completed_phases": _ALL_PHASES[:5],
            "failed_phase": "terminal_closeout",
        }
    if row in {"validation_replan", "validation_repair", "validation_host_contract"}:
        kind = "validation_failed"
    receipt = (
        None
        if row == "absent"
        else _validated_receipt(result_kind=LoopXTurnResultKind(kind), **kwargs)
    )
    if row in {"validation_replan", "validation_repair", "validation_host_contract"}:
        execution = _execution(result_kind=LoopXTurnResultKind.VALIDATION_FAILED)
        execution.update(validation_stage="host_result_contract" if row == "validation_host_contract" else "task_postcondition",
            validation={"schema_version": "loopx_turn_task_validation_v0", "ok": False, "status": "failed",
                "validator_kind": "fixture", "summary": "Declared postcondition absent", "errors": [],
                "recovery_kind": "replan_required" if row == "validation_replan" else "repair_required"})
        receipt = controller.ValidatedTurnReceipt.from_execution(execution)
    envelope = _envelope(
        should_run=route not in {"wait", "blocked"},
        quiet_noop_allowed=route == "wait",
        user_action_required=route == "user_action_required",
        selected_todo_id=todo,
        effective_action={
            "repair_required": "agent_workspace_repair",
            "replan_required": "autonomous_replan",
            "capability_action_required": "governed_capability_intent",
        }.get(route, "deliver"),
    )
    if route == "capability_action_required":
        envelope["action"]["capability_intent"] = {
            "schema_version": "pending_capability_intent_projection_v0",
            "goal_id": "goal-1",
            "agent_id": "agent-1",
            "command": "fixture command",
        }
    return dict(
        turn_receipt=receipt,
        quota_decision=envelope,
        predecessor_turn_key=receipt.turn_key if receipt else None,
        bounded_turn_budget=budget,
    )


@pytest.mark.parametrize(
    "row,route,expected",
    [
        (row, route, expected)
        for row, outcomes in _ROWS
        for route, expected in zip(_ROUTES, outcomes, strict=True)
    ],
)
def test_independent_full_decision_cells(row, route, expected):
    arguments = _cell(row, route)
    before = deepcopy(arguments["quota_decision"])
    if expected == "reject":
        with pytest.raises(
            ValueError, match="bounded turn budget|terminal Goal frontier evidence"
        ):
            controller.decide_loop_disposition(**arguments)
    else:
        result = controller.decide_loop_disposition(**arguments)
        _assert_markers(result, expected)
        assert ("replan_continuation" in result) == (expected == "replan")
        assert ("retry_continuation" in result) == (
            row == "host_retry"
            and route
            in {
                "ready_for_host",
                "capability_action_required",
                "wait",
                "blocked",
            }
        )
    assert arguments["quota_decision"] == before


def test_contract_projection_covers_all_routes_with_explicit_rejection():
    expected = dict(zip(_ROUTES, _PROJECTED, strict=True))
    expected["contract_error"] = None
    assert CONTRACT["route_projection"] == expected
    assert set(expected) == {route.value for route in LoopXTurnRoute}
    for route in LoopXTurnRoute:
        if route is LoopXTurnRoute.CONTRACT_ERROR:
            with pytest.raises(ValueError, match="envelope contract"):
                project_turn_route(route)
        else:
            assert (
                project_turn_route(route).value == expected[route.value]
            )


def test_contract_uses_closed_finite_partitions_and_unique_rules():
    contract = CONTRACT
    domains = dict(contract["partitions"])
    domains["route"] = [route.value for route in LoopXTurnRoute]
    domains["receipt_kind"] = ["absent", *[kind.value for kind in LoopXTurnResultKind]]
    assert domains["completion"] == [
        "no_followup",
        "successor",
        "active_goal",
        "not_applicable",
    ]
    assert domains["retry_state"] == [
        "available",
        "exhausted",
        "not_retryable",
        "not_applicable",
    ]
    assert domains["budget_state"] == [
        "absent",
        "available",
        "exhausted",
        "not_applicable",
    ]
    assert domains["validation_recovery"] == ["absent", "repair_required", "replan_required"]
    seen = set()
    checked = set()
    for rule in contract["rules"]:
        assert rule["id"] not in seen
        seen.add(rule["id"])
        for name, values in rule["when"].items():
            assert values and set(values) <= set(domains[name])
        if "check" in rule:
            assert set(rule) == {"id", "when", "check"}
            assert rule["check"] in contract["checks"]
            checked.add(rule["check"])
        else:
            assert rule["disposition"] in {
                "project_route",
                *[d.value for d in controller.LoopDisposition],
            }
            assert rule["lineage"] in {"decision", "effective", "receipt"}
            assert rule.get("extra") in {None, "capability", "iteration_stop", "retry"}
    assert checked == set(contract["checks"])
    # A newly added result kind requires an explicit independent expected row.
    covered = {
        row for row, _ in _ROWS if row in {kind.value for kind in LoopXTurnResultKind}
    }
    assert covered | {"validated_completion"} == {
        kind.value for kind in LoopXTurnResultKind
    }


def test_bundled_contract_is_the_one_consumed_by_controller():
    path = (
        Path(controller.__file__).parents[1] / "turn_loop_controller_contract_v0.json"
    )
    assert json.loads(path.read_text()) == CONTRACT


# The controller evaluates `rules` in order and stops at the first match, so a
# rule that refines another must stay in front of it. Key order inside one rule
# is immaterial (see the conjunction test above); this sequence is not.
_RULE_SEQUENCE = (
    "check_envelope",
    "check_decision_actor",
    "check_receipt_binding",
    "capability",
    "check_initial_terminal",
    "initial_terminal",
    "check_initial_todo",
    "initial_route",
    "check_completion_terminal",
    "completion_terminal",
    "check_completion_todo",
    "completion_successor",
    "completion_active_goal",
    "check_receipt_todo",
    "decision_user",
    "check_progress_budget",
    "progress_exhausted",
    "progress_route",
    "receipt_replan_required",
    "receipt_repair_required",
    "receipt_user_action_required",
    "receipt_wait",
    "receipt_iteration_failed",
    "host_replan",
    "host_repair",
    "check_host_retry",
    "host_retry",
    "host_exhausted",
    "task_validation_replan",
    "failed_receipt",
)


def test_contract_rule_sequence_is_pinned():
    """A reordered contract is a different program; regeneration must not hide it."""

    rules = CONTRACT["rules"]
    assert tuple(rule["id"] for rule in rules) == _RULE_SEQUENCE


# Rule-mutation sensitivity now runs in turn_loop_controller.test.ts, where the
# production TS evaluator consumes this contract. Python retains every semantic cell.
