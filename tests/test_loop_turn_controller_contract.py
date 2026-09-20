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
from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
from loopx.control_plane.turn_driver.transaction import LoopXTurnResultKind
from test_loop_turn_loop_controller import (
    _ALL_PHASES,
    _assert_markers,
    _budget,
    _envelope,
    _validated_receipt,
)


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
    receipt = (
        None
        if row == "absent"
        else _validated_receipt(result_kind=LoopXTurnResultKind(kind), **kwargs)
    )
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
    assert controller._LOOP_CONTROLLER_CONTRACT["route_projection"] == expected
    assert set(expected) == {route.value for route in LoopXTurnRoute}
    for route in LoopXTurnRoute:
        if route is LoopXTurnRoute.CONTRACT_ERROR:
            with pytest.raises(ValueError, match="envelope contract"):
                controller._route_to_disposition(route)
        else:
            assert (
                controller._route_to_disposition(route).value == expected[route.value]
            )


def test_contract_uses_closed_finite_partitions_and_unique_rules():
    contract = controller._LOOP_CONTROLLER_CONTRACT
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
    assert json.loads(path.read_text()) == controller._LOOP_CONTROLLER_CONTRACT


def test_conjunction_does_not_depend_on_json_object_key_order(monkeypatch):
    contract = deepcopy(controller._LOOP_CONTROLLER_CONTRACT)
    for rule in contract["rules"]:
        rule["when"] = dict(reversed(list(rule["when"].items())))
    monkeypatch.setattr(controller, "_LOOP_CONTROLLER_CONTRACT", contract)
    for row, outcomes in _ROWS:
        for route, expected in zip(_ROUTES, outcomes, strict=True):
            test_independent_full_decision_cells(row, route, expected)


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
    "failed_receipt",
)


def test_contract_rule_sequence_is_pinned():
    """A reordered contract is a different program; regeneration must not hide it."""

    rules = controller._LOOP_CONTROLLER_CONTRACT["rules"]
    assert tuple(rule["id"] for rule in rules) == _RULE_SEQUENCE


def _decisions_for(contract, monkeypatch):
    """Every cell's operator-visible decision: the disposition and its reason.

    Two rules can share a disposition and still explain it differently, so the
    reason is part of the observable decision, not commentary.
    """

    monkeypatch.setattr(controller, "_LOOP_CONTROLLER_CONTRACT", contract)
    observed = {}
    for row, outcomes in _ROWS:
        for route in _ROUTES:
            arguments = _cell(row, route)
            try:
                payload = controller.decide_loop_disposition(**arguments)
            except ValueError:
                observed[(row, route)] = ("reject", "reject")
            else:
                observed[(row, route)] = (
                    payload["disposition"],
                    str(payload["reason"]),
                )
    return observed


@pytest.mark.parametrize(
    "specific, general",
    [
        ("progress_exhausted", "progress_route"),
        ("host_replan", "failed_receipt"),
        ("host_repair", "failed_receipt"),
        ("host_retry", "failed_receipt"),
        ("host_exhausted", "failed_receipt"),
    ],
)
def test_a_refining_rule_must_precede_the_rule_it_refines(monkeypatch, specific, general):
    """Moving the specific rule behind the general one must change a real decision.

    This is what `_RULE_SEQUENCE` protects: each pinned precedence is load
    bearing, so the pin is not a restatement of the shipped file.
    """

    contract = deepcopy(controller._LOOP_CONTROLLER_CONTRACT)
    ids = [rule["id"] for rule in contract["rules"]]
    assert ids.index(specific) < ids.index(general)
    moved = next(rule for rule in contract["rules"] if rule["id"] == specific)
    rules = [rule for rule in contract["rules"] if rule["id"] != specific]
    rules.insert([rule["id"] for rule in rules].index(general) + 1, moved)
    contract["rules"] = rules

    shipped = _decisions_for(deepcopy(controller._LOOP_CONTROLLER_CONTRACT), monkeypatch)
    observed = _decisions_for(contract, monkeypatch)
    assert observed != shipped
