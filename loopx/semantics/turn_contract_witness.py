"""Fixed input/output witnesses for the shipped generated Turn contract.

Expected behavior is independently specified here, never enumerated from owner
values or table outputs. This establishes finite input liveness, not reachability
of every production trace or arbitrary forged-object safety.
"""

from __future__ import annotations

from .python_production import Production

# case, fresh route, expected disposition, expected reason fragment
_CONTROLLER_CASES = (
    ("absent", "ready", "run_now", "no prior receipt"),
    ("absent", "capability", "capability_action_required", "fresh capability intent"),
    ("absent", "blocked", "wait", "quiet no-spend wait"),
    ("absent", "repair", "repair", "fresh decision requires repair"),
    ("absent", "replan", "replan", "fresh decision requires replan"),
    ("absent", "user", "user_action_required", "fresh decision projects"),
    ("absent", "terminal", "terminal", "fresh Goal frontier proves"),
    ("completion_terminal", "terminal", "terminal", "durable no-follow-up"),
    ("completion_successor", "ready", "run_now", "declared successor"),
    ("completion_active", "ready", "run_now", "fresh Goal frontier"),
    ("validated_progress", "user", "user_action_required", "fresh decision projects"),
    ("exhausted_progress", "ready", "replan", "bounded turn budget exhausted"),
    ("validated_progress", "ready", "run_now", "validated progress"),
    ("replan_required", "ready", "replan", "turn receipt requires replan"),
    ("repair_required", "ready", "repair", "turn receipt requires repair"),
    ("user_action_required", "ready", "user_action_required", "turn receipt projects"),
    ("wait", "ready", "wait", "typed no-spend wait"),
    ("iteration_failed", "ready", "stop", "stop this iteration"),
    ("retry", "replan", "replan", "replan after host failure"),
    ("retry", "repair", "repair", "repair after host failure"),
    ("retry", "ready", "wait", "bounded backoff"),
    ("retry_exhausted", "ready", "repair", "exhausted its bounded attempt"),
    ("nonretry", "ready", "repair", "ended in host_failure"),
    ("host_failure", "ready", "repair", "ended in host_failure"),
    ("validation_failed", "ready", "repair", "ended in validation_failed"),
    ("writeback_failed", "ready", "repair", "ended in writeback_failed"),
    ("quota_spend_failed", "ready", "repair", "ended in quota_spend_failed"),
    (
        "terminal_closeout_failed",
        "ready",
        "repair",
        "ended in terminal_closeout_failed",
    ),
)
_PROJECTION_CASES = (
    ("ready_for_host", "run_now"),
    ("capability_action_required", "capability_action_required"),
    ("repair_required", "repair"),
    ("replan_required", "replan"),
    ("user_action_required", "user_action_required"),
    ("wait", "wait"),
    ("blocked", "wait"),
    ("contract_error", None),
)


def controller_input(case: str, route: str):
    """Construct synthetic qualified receipts, never touch goal/journal state."""
    from loopx.control_plane.quota.effective_action import EffectiveAction
    from loopx.control_plane.turn_driver import loop_controller as c
    from loopx.control_plane.turn_driver.host_failure import build_host_failure_record

    lineage = {
        "goal_id": "witness-goal",
        "agent_id": "witness-agent",
        "todo_id": "witness-todo",
    }
    receipt = None
    if case != "absent":
        kind = (
            "validated_completion"
            if case.startswith("completion_")
            else "host_failure"
            if case in {"retry", "retry_exhausted", "nonretry"}
            else "validated_progress"
            if case == "exhausted_progress"
            else case
        )
        material = kind in {"validated_completion", "validated_progress"}
        status = "committed" if material else "stopped"
        execution = {
            "schema_version": "loopx_turn_execution_v0",
            "result_kind": kind,
            "status": status,
            "receipt": {
                "schema_version": "loopx_turn_receipt_validation_v0",
                "ok": True,
                "result_kind": kind,
                "status": status,
                "turn_key": "witness-turn",
                "lineage": lineage,
                "settlement_effect_id": "witness-effect",
            },
        }
        if material:
            execution.update(
                effects={"state_written": True, "quota_spent": True},
                scheduler={"completed": True},
                settlement_result={
                    "ok": True,
                    "failure": None,
                    "receipts": [
                        {
                            "step_kind": step,
                            "effect_id": "witness-effect",
                            "status": "committed",
                        }
                        for step in ("validation", "durable_writeback", "quota_spend")
                    ],
                },
            )
        if kind == "validated_completion":
            continuation = {
                "completion_terminal": "no_followup",
                "completion_successor": "successor",
                "completion_active": "active_goal",
            }[case]
            execution["todo_completion"] = {
                "todo_id": "witness-todo",
                "continuation": continuation,
                "successor_todo_ids": ["witness-next"],
            }
        if case in {"retry", "retry_exhausted", "nonretry"}:
            execution["host_failure"] = build_host_failure_record(
                "auth_failed" if case == "nonretry" else "provider_capacity",
                attempt=3 if case == "retry_exhausted" else 1,
            )
        receipt = c.ValidatedTurnReceipt.from_execution(execution)
    todo = (
        "witness-next"
        if case in {"completion_successor", "completion_active"}
        else "witness-todo"
    )
    decision = {
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": lineage["goal_id"],
        "agent_id": lineage["agent_id"],
        "should_run": route not in {"blocked", "terminal"},
        "effective_action": {
            "capability": "governed_capability_intent",
            "repair": EffectiveAction.AGENT_WORKSPACE_REPAIR.value,
            "replan": "autonomous_replan",
            "terminal": "terminal_no_followup",
        }.get(route, "deliver"),
        "action_signature": {
            "matches": True,
            "source_hash": "sha256:witness",
            "envelope_hash": "sha256:witness",
        },
        "action": {
            "delivery_allowed": True,
            "must_attempt": True,
            "selected_todo": {"todo_id": todo},
        },
        "user": {"action_required": route == "user"},
    }
    if route == "terminal":
        decision["state"] = "terminal_no_followup"
    if route == "capability":
        decision["action"]["capability_intent"] = {
            "schema_version": "pending_capability_intent_projection_v0",
            "goal_id": lineage["goal_id"],
            "agent_id": lineage["agent_id"],
            "command": "fixture capability action",
        }
    return dict(
        turn_receipt=receipt,
        quota_decision=decision,
        predecessor_turn_key="witness-turn" if receipt else None,
        bounded_turn_budget=c.BoundedTurnBudget(
            lineage=lineage,
            max_turns=2,
            completed_turns=2 if case == "exhausted_progress" else 1,
        ),
    )


def probe_controller_production():
    from scripts.generate_turn_contract import (
        read_contract,
        validate_contract,
        verified_generated_paths,
    )
    from loopx.control_plane.turn_driver import (
        loop_controller as c,
        turn_contract_generated as shared,
    )

    verified_generated_paths()
    validate_contract(c._LOOP_CONTROLLER_CONTRACT)
    if (
        c._LOOP_CONTROLLER_CONTRACT is not shared.TURN_CONTROLLER_CONTRACT
        or c._LOOP_CONTROLLER_CONTRACT != read_contract()
    ):
        raise ValueError("controller is disconnected from its generated contract")
    site = "loopx/control_plane/turn_driver/loop_controller.py::decide_loop_disposition"
    rows = []
    for case, route, expected, reason in _CONTROLLER_CASES:
        actual = c.decide_loop_disposition(**controller_input(case, route))
        if actual.get("disposition") != expected or reason not in actual.get(
            "reason", ""
        ):
            raise ValueError(
                f"controller witness {case}/{route} violates the independent decision contract"
            )
        if any(
            actual.get(marker) is not False
            for marker in ("spends_quota", "writes_state", "launches_host")
        ):
            raise ValueError("controller witness grants unexpected effects")
        if (
            expected == "replan"
            and actual.get("replan_continuation", {}).get("stale_todo_rerun_allowed")
            is not False
        ):
            raise ValueError("controller witness lost bounded replan obligation")
        if (
            case == "retry"
            and route == "ready"
            and actual.get("retry_continuation", {}).get("retry_after_seconds") != 30
        ):
            raise ValueError("controller witness lost bounded retry obligation")
        rows.append(
            Production(
                site,
                c.decide_loop_disposition.__code__.co_firstlineno,
                "input_witness",
                frozenset({actual["disposition"]}),
                False,
            )
        )
    # Rejection is part of the complete function, not a disposition value.
    invalid = []
    x = controller_input("absent", "ready")
    x["quota_decision"]["action_signature"] = {}
    invalid.append(x)
    x = controller_input("absent", "ready")
    x["quota_decision"]["agent_id"] = ""
    invalid.append(x)
    x = controller_input("validated_progress", "ready")
    x["predecessor_turn_key"] = "stale"
    invalid.append(x)
    x = controller_input("absent", "terminal")
    x["quota_decision"].pop("state")
    invalid.append(x)
    x = controller_input("absent", "ready")
    x["quota_decision"]["action"]["selected_todo"] = None
    invalid.append(x)
    x = controller_input("completion_terminal", "ready")
    invalid.append(x)
    x = controller_input("completion_successor", "ready")
    x["quota_decision"]["action"]["selected_todo"] = {"todo_id": "undeclared"}
    invalid.append(x)
    x = controller_input("validated_progress", "ready")
    x["quota_decision"]["action"]["selected_todo"] = {"todo_id": "other"}
    invalid.append(x)
    x = controller_input("validated_progress", "ready")
    x["bounded_turn_budget"] = None
    invalid.append(x)
    for index, arguments in enumerate(invalid):
        try:
            c.decide_loop_disposition(**arguments)
        except ValueError:
            continue
        raise ValueError(f"controller accepted invalid witness {index}")
    return rows


def probe_projection_production():
    from loopx.control_plane.turn_driver import turn_contract_generated as shared

    rows = []
    for route, expected in _PROJECTION_CASES:
        try:
            actual = shared.project_turn_route(shared.LoopXTurnRoute(route))
        except ValueError:
            if expected is None:
                continue
            raise ValueError(
                f"public projection unexpectedly rejected {route}"
            ) from None
        if expected is None or actual.value != expected:
            raise ValueError(f"public projection witness disagrees on {route}")
        rows.append(
            Production(
                "loopx/control_plane/turn_driver/turn_contract_generated.py::project_turn_route",
                shared.project_turn_route.__code__.co_firstlineno,
                "input_witness",
                frozenset({actual.value}),
                False,
            )
        )
    return rows
