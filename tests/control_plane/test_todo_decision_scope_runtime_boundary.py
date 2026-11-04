from __future__ import annotations

from collections.abc import Callable

import pytest

from loopx.control_plane.todos import decision_scope
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result

SCOPE = {
    "schema_version": "decision_scope_v0",
    "kind": "write_scope",
    "granularity": "action",
    "scope_key": "release",
}
AGENT_ITEM = {
    "todo_id": "todo_work",
    "status": "open",
    "claimed_by": "agent-a",
    "required_decision_scopes": [SCOPE],
}
GATE = {
    "todo_id": "todo_gate",
    "status": "open",
    "task_class": "user_gate",
    "blocks_agent": "agent-a",
    "decision_scope": SCOPE,
    "unblocks_todo_id": "todo_work",
}


def _response(value: object) -> dict[str, object]:
    return {
        "schema_version": "todo_decision_scope_result_v0",
        "result": value,
    }


@pytest.mark.parametrize(
    ("invoke", "value"),
    [
        (
            lambda: decision_scope.build_required_decision_scope_consistency(
                {"first_open_items": [AGENT_ITEM]},
                {"first_open_items": [GATE]},
                agent_id="agent-a",
            ),
            {"schema_version": "wrong_consistency_v0"},
        ),
        (
            lambda: decision_scope.standing_decision_authority_for_agent(
                {"entries": [{"active": True, "blocks_agent": "agent-a"}]},
                agent_id="agent-a",
            ),
            {"schema_version": "wrong_standing_v0"},
        ),
        (lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM])[0][0], [[True]]),
        (
            lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM])[0][0],
            [[{"schema_version": "unknown_relation_v0"}]],
        ),
        (
            lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM]),
            {},
        ),
        (
            lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM]),
            [],
        ),
        (
            lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM]),
            [[]],
        ),
        (
            lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM]),
            [[None], [None]],
        ),
        (
            lambda: decision_scope.todo_gate_relations([GATE], [AGENT_ITEM]),
            [[{"schema_version": "unknown_relation_v0"}]],
        ),
    ],
)
def test_operation_specific_runtime_results_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[], object],
    value: object,
) -> None:
    monkeypatch.setattr(
        decision_scope,
        "effect_runtime_result",
        lambda *_args, **_kwargs: _response(value),
    )

    with pytest.raises(TypeError, match="invalid typed decision scope"):
        invoke()


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"schema_version": "wrong_result_v0", "result": True},
        {"schema_version": "todo_decision_scope_result_v0"},
    ],
)
def test_outer_runtime_envelope_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> None:
    monkeypatch.setattr(
        decision_scope,
        "effect_runtime_result",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(TypeError, match="invalid typed decision scope projection"):
        decision_scope.todo_gate_relations([GATE], [AGENT_ITEM])[0][0]


def test_nullable_operations_still_accept_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        decision_scope,
        "effect_runtime_result",
        lambda *_args, **_kwargs: _response([[None]]),
    )

    assert decision_scope.todo_gate_relations([GATE], [AGENT_ITEM])[0][0] is None
    monkeypatch.setattr(decision_scope, "effect_runtime_result", lambda *_args, **_kwargs: _response(None))
    assert decision_scope.select_scoped_gate_fallback(
        [GATE], [AGENT_ITEM], agent_id="agent-a", allow_unrelated_gate=True,
        monitor_debt_backoff_active=False,
    ) is None


@pytest.mark.parametrize("value", [True, [], {}, {"schema_version": "todo_gate_relation_v0"}])
def test_fallback_runtime_result_fails_closed(monkeypatch, value):
    monkeypatch.setattr(decision_scope, "effect_runtime_result", lambda *_args, **_kwargs: _response(value))
    with pytest.raises(TypeError, match="invalid typed decision scope fallback"):
        decision_scope.select_scoped_gate_fallback(
            [GATE], [AGENT_ITEM], agent_id="agent-a", allow_unrelated_gate=True,
            monitor_debt_backoff_active=False,
        )


@pytest.mark.parametrize("operation", ["covers", "scope_relation", "exact_relation", "relation"])
def test_retired_scalar_operations_are_rejected_by_real_runtime(operation: str) -> None:
    with pytest.raises(EffectRuntimeRejected, match="unsupported decision scope operation"):
        effect_runtime_result("todo.decision_scope.evaluate", {
            "schema_version": "todo_decision_scope_request_v0",
            "operation": operation,
            "gate_scope": SCOPE,
            "required_scope": SCOPE,
            "gate": {**GATE, "is_gate": True},
            "item": AGENT_ITEM,
        })


@pytest.mark.parametrize("value", [
    None, {}, {"schema_version": "wrong"},
    {"schema_version": "global_todo_readiness_v0", "candidates": {}, "warnings": []},
    {"schema_version": "global_todo_readiness_v0", "candidates": [], "warnings": [True]},
])
def test_global_readiness_transport_fails_closed_without_python_fallback(monkeypatch, value):
    calls = []

    def result(method, params):
        calls.append(method)
        return value

    monkeypatch.setattr(decision_scope, "effect_runtime_result", result)
    with pytest.raises(TypeError, match="invalid typed decision scope"):
        decision_scope.project_global_todo_readiness({"selected_todo": AGENT_ITEM})
    assert calls == ["todo.global_readiness.project"]


def test_global_readiness_request_bounds_codecs_and_reuses_gate_selection():
    gate = {**GATE, "text": "Confirm scope", "index": 1}
    request = decision_scope._global_readiness_request({
        "selected_todo": AGENT_ITEM,
        "normal_delivery_allowed": True,
        "unrelated": {"items": [{"todo_id": "unrelated"}]},
        "agent_todo_summary": {
            "future_list": [AGENT_ITEM, None],
            "metadata": {"todo_id": "metadata"},
        },
        "agent_scope_frontier": {"items": [AGENT_ITEM], "count": 1},
        "user_todo_summary": {"gate_open_items": [gate], "items": [dict(gate)]},
    })
    assert set(request["payload"]) == {
        "selected_todo", "agent_lane_next_action", "normal_delivery_allowed",
        "agent_todo_summary", "agent_scope_frontier",
    }
    assert request["payload"]["agent_todo_summary"] == {"future_list": [AGENT_ITEM, None]}
    assert set(request["decoded"]) == {
        "/selected_todo", "/agent_todo_summary/future_list/0", "/agent_scope_frontier/items/0",
    }
    assert request["gates"] == [decision_scope._readiness_facts(gate)]
