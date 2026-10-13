from __future__ import annotations

import json

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.goals import vision_checkpoint


def _runtime_result(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": vision_checkpoint.VISION_CHECKPOINT_SCHEMA_VERSION,
        "agent_id": "codex-main",
        "required": False,
        "satisfied": True,
        "decision": "not_required",
        "triggers": [],
        "delivery_boundary": "in_flight_continuation",
        **overrides,
    }


def _prepared_result(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": vision_checkpoint.VISION_REFRESH_PREPARED_SCHEMA_VERSION,
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "goal_id": "goal-main",
            "agent_id": "codex-main",
            "state": "vision_patch_proposed",
            "vision_patch": {"vision_summary": "Ship one bounded route."},
            "todo_delta": [],
            "vision_budget": {
                "schema_version": "goal_vision_budget_v0",
                "status": "ok",
                "field_limits": {"vision_summary": 420},
                "field_usage": {"vision_summary": 23},
                "total_limit": 1200,
                "total_usage": 23,
            },
            "validation": {
                "budget_checked": True,
                "budget_status": "ok",
                "write_correctness_checked": False,
            },
        },
        **overrides,
    }


def test_python_prepare_facade_sends_raw_provider_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def call(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return _prepared_result()

    monkeypatch.setattr(vision_checkpoint, "effect_runtime_result", call)
    result = vision_checkpoint.prepare_vision_refresh(
        {"vision_summary": " Ship one bounded route. "},
        goal_id="goal-main",
        agent_id="codex-main",
        existing_agent_vision=None,
        merge_patch=True,
        require_path_delta_for_durable_change=True,
    )

    assert result == _prepared_result()["agent_vision"]
    assert captured == {
        "method": "goal.vision_checkpoint.evaluate",
        "params": {
            "schema_version": vision_checkpoint.VISION_REFRESH_REQUEST_SCHEMA,
            "phase": "prepare",
            "goal_id": "goal-main",
            "agent_id": "codex-main",
            "agent_vision_packet": {
                "vision_summary": " Ship one bounded route. "
            },
            "existing_agent_vision": None,
            "merge_patch": True,
            "require_path_delta_for_durable_change": True,
            "allow_terminal_stride_path": False,
        },
    }


def test_python_facade_sends_explicit_boundary_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def call(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return _runtime_result()

    monkeypatch.setattr(vision_checkpoint, "effect_runtime_result", call)
    result = vision_checkpoint.build_vision_checkpoint(
        agent_id="codex-main",
        agent_vision=None,
        existing_agent_vision=None,
        vision_unchanged_reason=None,
        delivery_outcome="outcome_progress",
        active_state_next_action_update=None,
        delivery_boundary="in_flight_continuation",
        todo_id="todo_current001",
    )

    assert result == _runtime_result()
    assert captured["method"] == "goal.vision_checkpoint.evaluate"
    assert captured["params"] == {
        "schema_version": vision_checkpoint.VISION_REFRESH_REQUEST_SCHEMA,
        "phase": "finalize",
        "agent_id": "codex-main",
        "agent_vision": None,
        "existing_agent_vision": None,
        "vision_unchanged_reason": None,
        "delivery_outcome": "outcome_progress",
        "active_state_next_action_would_update": False,
        "delivery_boundary": "in_flight_continuation",
        "todo_id": "todo_current001",
        "completion_todo_id": None,
        "autonomous_replan_recorded": False,
        "blocked_retry": None,
    }


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {},
        _prepared_result(agent_vision=None),
        _prepared_result(agent_vision={}),
        _prepared_result(
            agent_vision={
                **_prepared_result()["agent_vision"],
                "vision_budget": None,
            }
        ),
    ],
)
def test_python_prepare_facade_rejects_malformed_runtime_results(
    monkeypatch, malformed: object
) -> None:
    monkeypatch.setattr(
        vision_checkpoint,
        "effect_runtime_result",
        lambda _method, _params: malformed,
    )
    with pytest.raises(RuntimeError, match="prepared result (must be|shape mismatch)"):
        vision_checkpoint.prepare_vision_refresh(
            {"vision_summary": "Ship one bounded route."},
            goal_id="goal-main",
            agent_id="codex-main",
            existing_agent_vision=None,
            merge_patch=False,
            require_path_delta_for_durable_change=False,
        )


def test_python_prepare_facade_preserves_typed_budget_rejection(monkeypatch) -> None:
    suggestion = 'compact "quoted" \\ value'

    def reject(_method: str, _params: dict[str, object]) -> object:
        raise effect_runtime.EffectRuntimeRejected(
            "vision_budget_exceeded: vision_summary uses 421 chars; limit is 420; "
            f"suggested compact value: {json.dumps(suggestion)}",
            diagnostic_code="vision_budget_exceeded",
        )

    monkeypatch.setattr(vision_checkpoint, "effect_runtime_result", reject)
    with pytest.raises(vision_checkpoint.GoalVisionBudgetError) as error:
        vision_checkpoint.prepare_vision_refresh(
            {"vision_summary": "x" * 421},
            goal_id="goal-main",
            agent_id="codex-main",
            existing_agent_vision=None,
            merge_patch=False,
            require_path_delta_for_durable_change=False,
        )
    assert error.value.field == "vision_summary"
    assert error.value.used == 421
    assert error.value.limit == 420
    assert error.value.suggestion == suggestion


def test_runtime_budget_rejection_keeps_actionable_suggestion() -> None:
    with pytest.raises(vision_checkpoint.GoalVisionBudgetError) as error:
        vision_checkpoint.prepare_vision_refresh(
            {"vision_summary": "x" * 421},
            goal_id="goal-main",
            agent_id="codex-main",
            existing_agent_vision=None,
            merge_patch=False,
            require_path_delta_for_durable_change=False,
        )

    assert error.value.field == "vision_summary"
    assert error.value.used == 421
    assert error.value.limit == 420
    assert error.value.suggestion == "x" * 93 + "..."
    assert len(str(error.value)) <= 240


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {},
        _runtime_result(required="false"),
        _runtime_result(decision="skip"),
        _runtime_result(triggers={}),
        _runtime_result(
            continuity_basis={
                "kind": "existing_vision_unchanged",
                "vision_generated_at": "",
            }
        ),
    ],
)
def test_python_facade_rejects_malformed_runtime_results(
    monkeypatch, malformed: object
) -> None:
    monkeypatch.setattr(
        vision_checkpoint,
        "effect_runtime_result",
        lambda _method, _params: malformed,
    )
    with pytest.raises(RuntimeError, match="result (must be|shape mismatch)"):
        vision_checkpoint.build_vision_checkpoint(
            agent_id=None,
            agent_vision=None,
            existing_agent_vision=None,
            vision_unchanged_reason=None,
            delivery_outcome=None,
            active_state_next_action_update=None,
        )


def test_python_facade_preserves_typed_rejection(monkeypatch) -> None:
    def reject(_method: str, _params: dict[str, object]) -> object:
        raise effect_runtime.EffectRuntimeRejected("typed checkpoint rejected")

    monkeypatch.setattr(vision_checkpoint, "effect_runtime_result", reject)
    with pytest.raises(ValueError, match="typed checkpoint rejected"):
        vision_checkpoint.build_vision_checkpoint(
            agent_id="codex-main",
            agent_vision=None,
            existing_agent_vision=None,
            vision_unchanged_reason=None,
            delivery_outcome="outcome_progress",
            active_state_next_action_update=None,
            delivery_boundary="in_flight_continuation",
            todo_id="todo_current001",
        )


def test_python_facade_preserves_typed_unchanged_revision() -> None:
    generated_at = "2026-08-22T18:30:17+08:00"

    result = vision_checkpoint.build_vision_checkpoint(
        agent_id="codex-main",
        agent_vision=None,
        existing_agent_vision={
            "agent_id": "codex-main",
            "state": "vision_active",
            "generated_at": generated_at,
        },
        vision_unchanged_reason="Validated evidence keeps the route intact.",
        delivery_outcome="outcome_progress",
        active_state_next_action_update=None,
    )

    assert result["decision"] == "unchanged_with_reason"
    assert result["continuity_basis"] == {
        "kind": "existing_vision_unchanged",
        "vision_generated_at": generated_at,
    }
