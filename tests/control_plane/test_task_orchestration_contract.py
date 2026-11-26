from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType
import pytest

from loopx.control_plane.agents.agent_scope_frontier import AgentScopeFrontierAction
from loopx.control_plane.quota import task_orchestration
from loopx.control_plane.quota.effective_action import EffectiveAction
from loopx.control_plane.work_items.work_lane import WORK_LANE_CONTRACT_SCHEMA_VERSION


REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_PATH = REPO_ROOT / "loopx" / "control_plane" / "quota" / "task_orchestration.py"


def _peer_todo(
    todo_id: str,
    *,
    claimed_by: str,
    resume_ready: bool | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "todo_id": todo_id,
        "status": "open",
        "task_class": "advancement_task",
        "priority": "P0",
        "text": f"Advance {todo_id}",
        "action_kind": "inspect",
        "claimed_by": claimed_by,
    }
    if resume_ready is not None:
        item["resume_when"] = "todo_done:dep"
        item["resume_ready"] = resume_ready
    return item


def test_task_orchestration_zero_any_and_single_owner_invariants() -> None:
    source = TARGET_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    any_names = [
        node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"
    ]
    assert any_names == []
    assert "type: ignore" not in source
    assert "noqa" not in source
    assert len(source.splitlines()) <= 550
    assert "work_lane_contract_v" + "1" not in source
    lane = task_orchestration._task_orchestration_work_lane_contract(
        {
            "eligible_peer_lanes": [],
            "coordinator_obligation": "coordinate_task_bundle",
        }
    )
    assert lane["schema_version"] == WORK_LANE_CONTRACT_SCHEMA_VERSION
    assert lane["action"] == "coordinate_task_bundle"


def test_task_orchestration_contract_is_actionable_and_material_change_stop() -> None:
    assert task_orchestration.task_orchestration_contract_is_actionable(None) is False
    assert task_orchestration.task_orchestration_contract_is_actionable({}) is True
    assert (
        task_orchestration.task_orchestration_contract_is_actionable(
            MappingProxyType({"execution_state": "ready"})
        )
        is True
    )
    assert (
        task_orchestration.task_orchestration_contract_is_actionable(
            MappingProxyType({"execution_state": "blocked"})
        )
        is False
    )

    blocked_peer = MappingProxyType(
        {
            "mode": "task_scoped_peer",
            "execution_state": "blocked",
            "retry_policy": "material_peer_state_change_only",
        }
    )
    assert (
        task_orchestration.task_orchestration_requires_material_change_stop(
            blocked_peer,
            effective_action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
        )
        is True
    )
    assert (
        task_orchestration.task_orchestration_requires_material_change_stop(
            blocked_peer,
            effective_action=EffectiveAction.NORMAL_RUN.value,
        )
        is False
    )
    assert (
        task_orchestration.task_orchestration_requires_material_change_stop(
            {"mode": "adaptive", "execution_state": "blocked"},
            effective_action=AgentScopeFrontierAction.AGENT_SCOPE_EXHAUSTED.value,
        )
        is False
    )


def test_apply_task_route_hint_override_preserves_input_and_supports_mapping_proxy() -> None:
    hint: dict[str, object] = {
        "current_agent_next_action": "wait_old",
        "goal_id": "goal-a",
    }
    assert task_orchestration.apply_task_route_hint_override(hint, None) is hint
    assert task_orchestration.apply_task_route_hint_override(None, {}) is None

    patched = task_orchestration.apply_task_route_hint_override(
        hint,
        MappingProxyType(
            {
                "omit_current_agent_next_action": True,
                "goal_id": "goal-b",
                "route_mode": "peer_handoff",
            }
        ),
    )
    assert patched == {"goal_id": "goal-b", "route_mode": "peer_handoff"}
    assert "current_agent_next_action" in hint

    reassigned = task_orchestration.apply_task_route_hint_override(
        hint,
        {
            "omit_current_agent_next_action": True,
            "current_agent_next_action": "coordinate_now",
        },
    )
    assert reassigned == {
        "goal_id": "goal-a",
        "current_agent_next_action": "coordinate_now",
    }


def test_payload_work_lane_contract_deferral_recovery_and_mapping_proxy() -> None:
    base_lane = MappingProxyType(
        {
            "schema_version": "",
            "lane": "advancement_task",
            "next_lane": "monitor_task",
            "obligation": "complete_advancement",
            "must_attempt_work": True,
            "reason_codes": [
                "advancement_ready",
                "  ",
                "agent_scope_no_current_runnable_candidate",
            ],
        }
    )
    assert (
        task_orchestration.payload_work_lane_contract(
            base_lane,
            effective_action=EffectiveAction.OUTCOME_FLOOR_RECOVERY.value,
            recovery_allowed=True,
            agent_scope_frontier={"recommended_action": "recover"},
        )
        is None
    )

    deferred = task_orchestration.payload_work_lane_contract(
        base_lane,
        effective_action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
        recovery_allowed=False,
        agent_scope_frontier={"reason": "peer hold active"},
    )
    assert deferred is not None
    assert deferred["schema_version"] == WORK_LANE_CONTRACT_SCHEMA_VERSION
    assert deferred["lane"] == AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value
    assert deferred["next_lane"] == "advancement_task"
    assert deferred["must_attempt_work"] is False
    assert deferred["reason_codes"] == [
        "advancement_ready",
        "agent_scope_no_current_runnable_candidate",
        AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
    ]
    assert deferred["deferred_work_lane"] == {
        "lane": "advancement_task",
        "next_lane": "monitor_task",
        "obligation": "complete_advancement",
        "reason_codes": base_lane["reason_codes"],
    }
    assert deferred["action"] == "peer hold active"

    copied = task_orchestration.payload_work_lane_contract(
        MappingProxyType({"lane": "monitor_task", "must_attempt_work": False}),
        effective_action=EffectiveAction.MONITOR_DUE.value,
        recovery_allowed=False,
        agent_scope_frontier=None,
    )
    assert isinstance(copied, dict)
    assert copied == {"lane": "monitor_task", "must_attempt_work": False}
    assert (
        task_orchestration.payload_work_lane_contract(
            None,
            effective_action=EffectiveAction.NORMAL_RUN.value,
            recovery_allowed=False,
            agent_scope_frontier=None,
        )
        is None
    )


def test_build_quota_work_lane_and_attach_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        task_orchestration,
        "latest_unchanged_monitor_observation",
        lambda *_args, **_kwargs: {"run_id": "run-1"},
    )

    def _fake_context(
        item: dict[str, object],
        *,
        agent_todo_summary: dict[str, object] | None,
        monitor_due_item_limit: int,
        monitor_attempt_already_recorded: bool,
        monitor_debt_backoff_active: bool,
        advancement_allowed: bool,
    ) -> dict[str, object]:
        captured.update(
            {
                "item": item,
                "agent_todo_summary": agent_todo_summary,
                "monitor_due_item_limit": monitor_due_item_limit,
                "monitor_attempt_already_recorded": monitor_attempt_already_recorded,
                "monitor_debt_backoff_active": monitor_debt_backoff_active,
                "advancement_allowed": advancement_allowed,
            }
        )
        return {"lane": "monitor_backoff"}

    monkeypatch.setattr(
        task_orchestration,
        "build_work_lane_context_contract",
        _fake_context,
    )
    lane = task_orchestration.build_quota_work_lane_contract(
        {"goal_id": "goal-1"},
        status_payload={"recent_runs": []},
        goal_id="goal-1",
        agent_id="codex-main",
        agent_todo_summary={"items": []},
        monitor_due_item_limit=3,
        monitor_debt_arbitration=MappingProxyType({"active": True}),
        advancement_allowed=False,
    )
    assert lane == {"lane": "monitor_backoff"}
    assert captured["monitor_attempt_already_recorded"] is True
    assert captured["monitor_debt_backoff_active"] is True
    assert captured["advancement_allowed"] is False

    payload: dict[str, object] = {"ok": True}
    assert task_orchestration.attach_task_orchestration_payload(payload, None) == {
        "ok": True
    }
    task_orchestration.attach_task_orchestration_payload(
        payload, MappingProxyType({"mode": "task_scoped_peer"})
    )
    assert isinstance(payload["task_orchestration_contract"], dict)
    assert payload["task_orchestration_contract"] == {"mode": "task_scoped_peer"}


def test_apply_task_orchestration_peer_order_empty_precedence_and_fallbacks() -> None:
    fallback: dict[str, object] = {"lane": "advancement_task"}
    boundary: dict[str, object] = {
        "peer_task_coordination": {
            "enabled": True,
            "coordinator_agent_id": "codex-main",
        }
    }
    identity: dict[str, object] = {
        "agent_id": "codex-main",
        "registered_agents": ["codex-main", "peer-b", "peer-a"],
    }
    mgmt = MappingProxyType(
        {
            "agents": [
                {"agent_id": "peer-a", "state": "running"},
                {"agent_id": "peer-b", "state": "running"},
            ]
        }
    )

    items_first = [
        _peer_todo("t2", claimed_by="peer-b"),
        _peer_todo("t1", claimed_by="peer-a", resume_ready=False),
    ]
    items_reversed = list(reversed(items_first))

    contract_a, lane_a = task_orchestration.apply_task_orchestration_contract(
        fallback_work_lane_contract=fallback,
        goal_boundary=boundary,
        agent_identity=identity,
        agent_todo_summary={"items": items_first},
        raw_agent_todo_summary={"items": items_first},
        agent_todo_source_items=items_first,
        available_capabilities=[task_orchestration.PEER_AGENT_ACTIVATION_CAPABILITY],
        agent_management_projection=mgmt,
    )
    contract_b, _ = task_orchestration.apply_task_orchestration_contract(
        fallback_work_lane_contract=fallback,
        goal_boundary=boundary,
        agent_identity=identity,
        agent_todo_summary={"items": items_reversed},
        raw_agent_todo_summary={"items": items_reversed},
        agent_todo_source_items=items_reversed,
        available_capabilities=[task_orchestration.PEER_AGENT_ACTIVATION_CAPABILITY],
        agent_management_projection=mgmt,
    )
    assert contract_a is not None and contract_b is not None
    assert contract_a["assignment_key"] == contract_b["assignment_key"]
    assert lane_a is not None and lane_a["reason_codes"] == ["eligible_peer_lanes"]

    empty_contract, empty_lane = task_orchestration.apply_task_orchestration_contract(
        fallback_work_lane_contract=fallback,
        goal_boundary=boundary,
        agent_identity=identity,
        agent_todo_summary={"items": items_first},
        raw_agent_todo_summary={"items": items_first},
        agent_todo_source_items=[],
        available_capabilities=[task_orchestration.PEER_AGENT_ACTIVATION_CAPABILITY],
        agent_management_projection=mgmt,
    )
    assert empty_contract is None
    assert empty_lane is fallback

    # Boolean max_children fails safely without throwing.
    bool_contract, bool_lane = task_orchestration.apply_task_orchestration_contract(
        fallback_work_lane_contract=fallback,
        goal_boundary={
            "orchestration": {
                "mode": "multi_subagent",
                "spawn_allowed": True,
                "max_children": True,
            }
        },
        agent_identity=identity,
        agent_todo_summary=None,
        available_capabilities=[task_orchestration.SUBAGENT_SPAWN_CAPABILITY],
    )
    assert bool_contract is None
    assert bool_lane is fallback

    adaptive_lane = task_orchestration._task_orchestration_work_lane_contract(
        MappingProxyType(
            {
                "mode": "adaptive",
                "eligible_child_lanes": [{"todo_id": "t1"}, {"todo_id": "t2"}],
                "coordinator_obligation": "spawn_children",
            }
        )
    )
    assert adaptive_lane["reason_codes"] == ["eligible_child_lanes"]
    assert adaptive_lane["eligible_peer_lane_count"] == 2

    hybrid_items = [
        _peer_todo("t_primary", claimed_by="codex-main"),
        _peer_todo("t_child", claimed_by="codex-main"),
        _peer_todo("t_peer", claimed_by="peer-a", resume_ready=False),
    ]
    hybrid_contract, hybrid_lane = task_orchestration.apply_task_orchestration_contract(
        fallback_work_lane_contract=fallback,
        goal_boundary={
            "write_scope": ["loopx/**"],
            "peer_task_coordination": {
                "enabled": True,
                "coordinator_agent_id": "codex-main",
            },
            "orchestration": {
                "mode": "multi_subagent",
                "spawn_allowed": True,
                "max_children": 2,
            },
        },
        agent_identity={
            "agent_id": "codex-main",
            "registered_agents": ["codex-main", "peer-a"],
        },
        agent_todo_summary={"items": hybrid_items},
        raw_agent_todo_summary={"items": hybrid_items},
        agent_todo_source_items=hybrid_items,
        available_capabilities=[task_orchestration.SUBAGENT_SPAWN_CAPABILITY],
        agent_management_projection=MappingProxyType(
            {"agents": [{"agent_id": "peer-a", "state": "running"}]}
        ),
    )
    assert hybrid_contract is not None
    assert hybrid_contract["mode"] == "adaptive"
    assert "peer_activation_diagnostic" in hybrid_contract
    assert hybrid_lane is not None
    assert hybrid_lane["reason_codes"] == ["eligible_child_lanes"]


def test_registered_peer_malformed_runtime_result_returns_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback: dict[str, object] = {"lane": "advancement_task"}
    monkeypatch.setattr(
        task_orchestration,
        "effect_runtime_result",
        lambda *_args, **_kwargs: "invalid",
    )
    contract, lane = task_orchestration.apply_task_orchestration_contract(
        fallback_work_lane_contract=fallback,
        goal_boundary={
            "peer_task_coordination": {
                "enabled": True,
                "coordinator_agent_id": "codex-main",
            }
        },
        agent_identity={
            "agent_id": "codex-main",
            "registered_agents": ["codex-main", "peer-a"],
        },
        agent_todo_summary={"items": [_peer_todo("t1", claimed_by="peer-a")]},
        agent_todo_source_items=[_peer_todo("t1", claimed_by="peer-a")],
        available_capabilities=[task_orchestration.PEER_AGENT_ACTIVATION_CAPABILITY],
    )
    assert contract is None
    assert lane is fallback
