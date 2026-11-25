from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

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


@pytest.fixture
def runtime_operations(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    operations: list[object] = []

    def record(method: str, params: Mapping[str, object]) -> object:
        operations.append(params["operation"])
        return effect_runtime_result(method, params)

    monkeypatch.setattr(decision_scope, "effect_runtime_result", record)
    return operations


def test_consistency_and_repair_share_one_real_runtime_call(runtime_operations: list[object]) -> None:
    consistency = decision_scope.build_required_decision_scope_consistency(
        None, None, agent_id="agent-a", agent_source_items=[AGENT_ITEM],
    )
    hint = decision_scope.build_required_decision_scope_repair_hint(consistency)

    assert hint is not None
    assert hint["consistency"] is consistency
    assert hint["trigger"] == "required_decision_scope_projection_drift"
    assert runtime_operations == ["consistency"]
    assert set(consistency) == {
        "schema_version", "ok", "status", "agent_id", "checked_agent_todo_count",
        "checked_required_scope_count", "terminal_outcome_count",
        "standing_authority_match_count", "errors",
    }


@pytest.mark.parametrize("copy_as_dict", [False, True], ids=["edited-errors", "plain-dict"])
def test_repair_rechecks_a_changed_or_uncached_projection(
    runtime_operations: list[object],
    copy_as_dict: bool,
) -> None:
    consistency = decision_scope.build_required_decision_scope_consistency(
        None, None, agent_id="agent-a", agent_source_items=[AGENT_ITEM],
    )
    if copy_as_dict:
        consistency = dict(consistency)
        expected = "required_decision_scope_projection_drift"
    else:
        errors = consistency["errors"]
        assert isinstance(errors, list)
        errors[0]["reason_code"] = "multi_agent_user_gate_missing_scope"
        expected = "user_gate_scope_projection_drift"

    hint = decision_scope.build_required_decision_scope_repair_hint(consistency)

    assert hint is not None
    assert hint["trigger"] == expected
    assert runtime_operations == ["consistency", "repair_hint"]


def test_consistency_envelope_without_plan_uses_standalone_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    operations = []

    def legacy_envelope(method: str, params: Mapping[str, object]) -> object:
        operations.append(params["operation"])
        response = effect_runtime_result(method, params)
        if params["operation"] == "consistency":
            return _response(response["result"])
        return response

    monkeypatch.setattr(decision_scope, "effect_runtime_result", legacy_envelope)
    consistency = decision_scope.build_required_decision_scope_consistency(
        None, None, agent_id="agent-a", agent_source_items=[AGENT_ITEM],
    )
    hint = decision_scope.build_required_decision_scope_repair_hint(consistency)

    assert hint is not None
    assert hint["trigger"] == "required_decision_scope_projection_drift"
    assert operations == ["consistency", "repair_hint"]


@pytest.mark.parametrize("consistency", [{}, {"ok": True}, {"ok": 0}, {"ok": None}])
def test_repair_short_circuits_unless_ok_is_false(
    runtime_operations: list[object], consistency: dict[str, object],
) -> None:
    assert decision_scope.build_required_decision_scope_repair_hint(consistency) is None
    assert runtime_operations == []


@pytest.mark.parametrize("bad_response", [
    {},
    _response(None),
    {**_response(None), "repair_plan": None},
    {**_response(None), "repair_plan": []},
    *[{**_response(None), "repair_plan": {
        "schema_version": "todo_decision_scope_repair_plan_v0", "trigger": "repair",
        "recommended_mode": "repair", "blocked_action_scope": "scope",
        "allowed": True, "notify": "DONT_NOTIFY", "reason": "repair required",
        "repair_focus": "scope", "spend_policy": "after writeback",
        **patch,
    }} for patch in (
        {"schema_version": "unknown_plan_v0"}, {"allowed": 1}, {"reason": []}, {"source": "unexpected"},
    )],
])
def test_malformed_repair_runtime_response_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    bad_response: dict[str, object],
) -> None:
    monkeypatch.setattr(decision_scope, "effect_runtime_result", lambda *_args: bad_response)

    with pytest.raises(TypeError, match="^invalid typed decision scope repair_hint projection$"):
        decision_scope.build_required_decision_scope_repair_hint({"ok": False, "errors": []})


def test_decision_scope_codec_has_no_any_annotations() -> None:
    from loopx.canary.maintainability_ratchet import module_metrics

    metrics = module_metrics(Path(decision_scope.__file__))
    assert metrics["any_count"] == metrics["dict_any_count"] == 0


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
