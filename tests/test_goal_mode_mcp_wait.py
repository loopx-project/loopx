from __future__ import annotations
import copy
import json
from dataclasses import replace
import pytest
from loopx.control_plane.host_adapter_settlement import (
    HostTodoSettlementRequest,
    _request_payload,
    _runtime_reduction,
    settle_host_todo_wait,
)
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane


def request():
    return HostTodoSettlementRequest(
        goal_id="wait-goal",
        agent_id="claude",
        todo_id="todo_wait01",
        runtime_profile="claude_code",
        legacy_host_surface="claude_code",
        scheduler_owner="agent_cli_loop",
        execution_mode="interactive",
        completion_args=(),
        turn_instance_id="original-work",
    )


def guard():
    r = request()
    identity = {
        "schema_version": "quota_settlement_identity_v0",
        "goal_id": r.goal_id,
        "agent_id": r.agent_id,
        "todo_id": r.todo_id,
        "turn_instance_id": r.turn_instance_id,
        "effect_id": "wait-goal:claude:todo_wait01:original-work",
    }
    return {
        "ok": True,
        "should_run": True,
        "normal_delivery_allowed": True,
        "work_input_restored": True,
        "selected_todo": {"todo_id": r.todo_id, "status": "open"},
        "heartbeat_receipt": {"settlement_identity": identity},
    }


def test_native_wait_executes_only_current_original_guard():
    calls = []

    def cli(args, **kwargs):
        calls.append(args)
        assert args[:2] == ["quota", "should-run"] and "original-work" in args
        assert "spend-slot" not in args and "complete" not in args
        return json.dumps(guard())

    r = json.loads(settle_host_todo_wait(request(), "Dependency pending.", run_cli=cli))
    assert (
        len(calls) == 1
        and r["ok"]
        and not r["completed"]
        and not r["settlement_complete"]
        and r["result_kind"] == "wait"
    )
    assert r["effects"] == {
        "host_invoked": False,
        "todo_completed": False,
        "state_written": False,
        "quota_spent": False,
    }
    assert (
        r["task_validation"]["status"] == "not_required"
        and r["settlement_identity"]["turn_instance_id"] == "original-work"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "stop",
        "permission",
        "unrestored",
        "done",
        "foreign_todo",
        "foreign_turn",
        "foreign_agent",
        "malformed",
    ],
)
def test_native_wait_refuses_unadmitted_or_replaced_provider_facts(fault):
    g = copy.deepcopy(guard())
    if fault == "stop":
        g["should_run"] = False
    elif fault == "permission":
        g["normal_delivery_allowed"] = False
    elif fault == "unrestored":
        g["work_input_restored"] = False
    elif fault == "done":
        g["selected_todo"]["status"] = "done"
    elif fault == "foreign_todo":
        g["selected_todo"]["todo_id"] = "todo_other01"
    elif fault in ["foreign_turn", "foreign_agent"]:
        i = g["heartbeat_receipt"]["settlement_identity"]
        i["turn_instance_id" if fault == "foreign_turn" else "agent_id"] = "other"
        i["effect_id"] = ":".join(
            i[k] for k in ["goal_id", "agent_id", "todo_id", "turn_instance_id"]
        )
    p = {
        **_request_payload(request(), phase="wait"),
        "wait_reason": "Dependency pending.",
        "guard_output": "broken JSON" if fault == "malformed" else json.dumps(g),
    }
    assert _runtime_reduction(p, phase="wait")["result"]["ok"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("completion_args", ["todo", "complete"]),
        ("no_follow_up", True),
        ("vision_unchanged_reason", "Old route."),
        ("wait_reason", "x" * 241),
    ],
)
def test_wait_cannot_smuggle_completion_or_judgment(field, value):
    p = {**_request_payload(request(), phase="wait"), "wait_reason": "Pending."}
    p[field] = value
    with pytest.raises(ValueError):
        _runtime_reduction(p, phase="wait")


def test_wait_does_not_allocate_a_turn_or_cross_cli_without_admission():
    r = request()
    r = replace(r, turn_instance_id=None)

    def cli(*a, **kw):
        pytest.fail("unadmitted wait invoked CLI")

    assert not json.loads(settle_host_todo_wait(r, "Pending.", run_cli=cli))["ok"]


def test_wait_keeps_bound_agent_fence_before_transport(monkeypatch):
    c = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="wait",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {"goal_id": "wait-goal", "registry": "unused", "agent_id": "claude"},
    )
    monkeypatch.setattr(
        c, "run_cli", lambda *a, **kw: pytest.fail("wrong Agent invoked CLI")
    )
    assert not json.loads(c.wait_task("todo_wait01", "foreign", "Pending."))["ok"]
