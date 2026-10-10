from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.control_plane.quota import live_decision
from loopx.control_plane.quota.effect_program import ReceiptBoundReplayPhase
from loopx.control_plane.effect_program import (
    interpret_quota_should_run_packet,
)
from loopx.control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    dispatch_turn_start_hooks,
)
from loopx.control_plane.quota.live_decision import (
    bind_action_selection_cli_routes,
    build_live_quota_should_run_decision,
)
from loopx.control_plane.quota.error_codes import (
    HeartbeatReceiptIdentityConflictError,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.rollout_event_log import build_rollout_event

GOAL_ID = "effect-interpreter-fixture"


def _turn_start_dispatch(
    *,
    required: bool = True,
    observation_count: int = 1,
    commands: tuple[str, ...] = ("loopx inbox drain --goal-id fixture",),
) -> dict[str, object]:
    hooks: list[TurnStartHookRegistration] = []
    for index, command in enumerate(commands):
        hook_id = f"operator_inbox.turn_start_sync_{index}"

        def produce(
            *, hook_id: str = hook_id, required: bool = required
        ) -> dict[str, object]:
            return {
                "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
                "hook_id": hook_id,
                "capability_id": "operator-inbox",
                "phase": "turn_start",
                "status": "observed" if required else "empty",
                "observation_count": observation_count if required else 0,
                "agent_read_required": required,
                "external_reads_performed": True,
                "external_writes_performed": False,
                "local_private_state_mutated": required,
                "private_content_returned": False,
                "provider_payload_returned": False,
                "error_code": None,
            }

        hooks.append(
            TurnStartHookRegistration(
                hook_id=hook_id,
                capability_id="operator-inbox",
                requested_read_scope=("provider_history",),
                requested_write_scope=("owner_private_inbox",),
                producer=produce,
                required_read={
                    "kind": "operator_inbox",
                    "command": command,
                    "reason": "turn-start hook synchronized new operator inbox evidence",
                    "ordering": "before_work",
                },
            )
        )
    return dispatch_turn_start_hooks(hooks)


def _ordinary_status_payload() -> dict[str, object]:
    todo_text = "[P1] Keep advancing the selected task."
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": "todo_ordinary_work",
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )


def _material_review_urgency(
    *, reply_due: bool = False, pending_count: int = 1, **_kwargs: object
) -> dict[str, object]:
    return {
        "schema_version": "operator_inbox_urgency_v0",
        "enabled": True,
        "pending_count": pending_count,
        "attention_required_count": int(reply_due),
        "reply_due": reply_due,
        "material_review_count": 1,
        "material_attachment_count": 0,
        "material_review_due": True,
        "material_review_drain_limit": 20,
        "local_private_content_returned": False,
    }


def _inbox_goal(tmp_path: Path) -> dict[str, object]:
    return {
        "id": GOAL_ID,
        "registry_member": True,
        "status": "active",
        "adapter_kind": "harness_self_improvement",
        "adapter_status": "connected-read-only",
        "repo": str(tmp_path),
        "quota": {"compute": 1.0, "window_hours": 24},
        "control_plane": {
            "lark_event_inbox": {
                "enabled": True,
                "config_path": ".loopx/config/lark/inbox.json",
            }
        },
    }


def _status_with_inbox(tmp_path: Path) -> dict[str, object]:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"goals": [_inbox_goal(tmp_path)]}),
        encoding="utf-8",
    )
    return _ordinary_status_payload() | {"registry": str(registry_path)}


def test_live_quota_decision_maps_to_effect_turn(tmp_path: Path) -> None:
    todo_text = "[P1] Advance the bounded slice."
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )
    packet = build_live_quota_should_run_decision(
        payload,
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        scheduler_execution_context={
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    )
    turn = interpret_quota_should_run_packet(
        packet,
        goal_id=GOAL_ID,
        agent_id=None,
        capabilities=["shell"],
    )

    assert turn.observation.decision == "run"
    assert turn.observation.effective_action == "normal_run"
    assert turn.interpretation.route == "advancement_task"
    assert turn.interpretation.obligation == "advance_one_bounded_segment"
    assert turn.interpretation.interaction_mode == "bounded_delivery"
    assert turn.next_effect.cli_actions
    assert turn.next_effect.cli_actions[0].startswith("loopx --runtime-root ")


@pytest.mark.parametrize("reads", [False, True])
def test_managed_turn_projects_prior_unsettled_heartbeat_recovery(
    tmp_path: Path, reads: bool,
) -> None:
    runtime_root = tmp_path / "runtime"
    agent_id = "codex-fixture"
    todo_id = "todo_ordinary_work"
    prior_turn_id = "managed-prior-turn"
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        "# Goal\n\n## Agent Todo\n\n- [ ] Keep advancing the selected task.\n"
        f"  <!-- loopx:todo todo_id={todo_id} status=open "
        "task_class=advancement_task -->\n",
        encoding="utf-8",
    )
    (tmp_path / "registry.json").write_text(json.dumps({
        "common_runtime_root": str(runtime_root),
        "goals": [{"id": GOAL_ID, "repo": str(tmp_path),
                   "state_file": str(state_path)}],
    }), encoding="utf-8")
    event = build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="quota_should_run",
        agent_id=agent_id,
        todo_id=todo_id,
        run_id=prior_turn_id,
        status="normal_run",
        summary="managed heartbeat guard requires closeout",
        details={
            "todo_id": todo_id,
            "settlement_effect_id": (
                f"{GOAL_ID}:{agent_id}:{todo_id}:{prior_turn_id}"
            ),
            "closeout_required": True,
        },
    )
    log_path = runtime_root / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    todo_text = "[P1] Keep advancing the selected task."
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": todo_id,
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
        coordination={
            "registered_agents": [agent_id],
            "agent_model": "peer_v1",
        },
        claim_scope_agent_id=agent_id,
    )
    packet = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=agent_id,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=runtime_root,
        turn_start_hook_dispatch=_turn_start_dispatch(required=reads),
        route_source="loopx_turn_plan",
        turn_instance_id="managed-current-turn",
        scheduler_execution_context={
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    )

    assert packet["runtime_root"] == str(runtime_root)
    assert packet["effective_action"] == "unsettled_host_turn_recovery"
    assert packet["unsettled_host_turn_recovery"]["prior_turn_instance_id"] == (
        prior_turn_id
    )
    contract = packet["interaction_contract"]
    assert contract["mode"] == "unsettled_host_turn_recovery"
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["cli_channel"]["spend_after_validation"] is False


def test_settled_turn_defers_prior_turn_recovery_to_a_fresh_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"goals": []}), encoding="utf-8")

    monkeypatch.setattr(
        live_decision,
        "read_heartbeat_settlement",
        lambda *_args, **_kwargs: SimpleNamespace(
            monitor_phase=None,
            replay_phase=ReceiptBoundReplayPhase.SETTLED,
        ),
    )

    def fail_if_recovery_runs(*_args: object, **_kwargs: object) -> bool:
        pytest.fail("a settled host Turn must not inspect prior-Turn recovery")

    monkeypatch.setattr(
        live_decision,
        "apply_unsettled_host_turn_recovery_if_required",
        fail_if_recovery_runs,
    )
    todo_text = "[P1] Keep advancing the selected task."
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": "todo_ordinary_work",
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
        coordination={
            "registered_agents": ["codex-fixture"],
            "agent_model": "peer_v1",
        },
        claim_scope_agent_id="codex-fixture",
    )

    packet = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id="codex-fixture",
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=registry_path,
        runtime_root=runtime_root,
        route_source="loopx_turn_plan",
        receipt_bound_todo_id="todo_ordinary_work",
        turn_instance_id="managed-settled-turn",
        scheduler_execution_context={
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    )

    assert packet["decision"] == "skip"
    assert packet["effective_action"] == "heartbeat_settled_skip"
    assert packet["should_run"] is False
    assert packet.get("selected_todo") is None
    assert packet.get("unsettled_host_turn_recovery") is None


def test_managed_turn_accepts_exact_material_monitor_poll_closeout(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    agent_id = "codex-fixture"
    monitor_id = "todo_managed_monitor"
    prior_turn_id = "managed-prior-monitor-turn"
    state_path.write_text(
        "# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P0-monitor] Observe the managed target.\n"
        f"  <!-- loopx:todo todo_id={monitor_id} status=open "
        "task_class=continuous_monitor target_key=managed-target "
        "cadence=1h next_due_at=2099-01-01T00%3A00%3A00Z -->\n"
        "- [ ] [P1] Continue independent work.\n"
        "  <!-- loopx:todo todo_id=todo_visible status=open "
        "task_class=advancement_task -->\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path),
                        "state_file": str(state_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="quota_should_run",
        agent_id=agent_id,
        todo_id=monitor_id,
        run_id=prior_turn_id,
        status="normal_run",
        summary="managed monitor heartbeat requires closeout",
        details={
            "todo_id": monitor_id,
            "settlement_effect_id": (
                f"{GOAL_ID}:{agent_id}:{monitor_id}:{prior_turn_id}"
            ),
            "closeout_required": True,
        },
    )
    goal_runtime = runtime_root / "goals" / GOAL_ID
    goal_runtime.mkdir(parents=True)
    (goal_runtime / "rollout-event-log.jsonl").write_text(
        json.dumps(receipt) + "\n", encoding="utf-8"
    )
    runs_dir = goal_runtime / "runs"
    runs_dir.mkdir()
    (runs_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "classification": "quota_monitor_poll",
                "goal_id": GOAL_ID,
                "agent_id": agent_id,
                "todo_id": monitor_id,
                "turn_instance_id": prior_turn_id,
                "material_change": True,
                "quota_monitor_poll_commit": {
                    "schema_version": "quota_monitor_poll_commit_receipt_v0",
                    "effect_id": (
                        f"quota-monitor-poll:{GOAL_ID}:{agent_id}:"
                        f"{prior_turn_id}:todo:{monitor_id}"
                    ),
                    "request_digest": "sha256:fixture",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": "todo_visible",
                "index": 2,
                "text": "[P1] Continue independent work.",
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action="[P1] Continue independent work.",
        next_action="[P1] Continue independent work.",
        coordination={"registered_agents": [agent_id], "agent_model": "peer_v1"},
        claim_scope_agent_id=agent_id,
    )
    packet = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=agent_id,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=registry_path,
        runtime_root=runtime_root,
        route_source="loopx_turn_plan",
        turn_instance_id="managed-current-turn",
        scheduler_execution_context={
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    )

    assert packet["effective_action"] != "unsettled_host_turn_recovery"
    assert packet["selected_todo"]["todo_id"] == "todo_visible"


def test_recovery_reads_lifecycle_when_status_summary_omits_bound_todo(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    agent_id = "codex-fixture"
    todo_id = "todo_lifecycle_boundary"
    prior_turn_id = "managed-prior-turn"
    state_path.write_text(
        "# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Closed by an external lifecycle transition.\n"
        f"  <!-- loopx:todo todo_id={todo_id} status=blocked "
        "task_class=advancement_task -->\n"
        "- [ ] [P1] Continue an unrelated visible item.\n"
        "  <!-- loopx:todo todo_id=todo_visible status=open "
        "task_class=advancement_task -->\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path),
                        "state_file": str(state_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    event = build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="quota_should_run",
        agent_id=agent_id,
        todo_id=todo_id,
        run_id=prior_turn_id,
        status="normal_run",
        summary="managed heartbeat guard requires closeout",
        details={
            "todo_id": todo_id,
            "settlement_effect_id": f"{GOAL_ID}:{agent_id}:{todo_id}:{prior_turn_id}",
            "closeout_required": True,
        },
    )
    log_path = runtime_root / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    # This is the compact status shape that caused the regression: the bound
    # blocked Todo is absent from every visible lane, while an unrelated item
    # remains available for ordinary selection.
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": "todo_visible",
                "index": 2,
                "text": "[P1] Continue an unrelated visible item.",
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action="[P1] Continue an unrelated visible item.",
        next_action="[P1] Continue an unrelated visible item.",
        coordination={"registered_agents": [agent_id], "agent_model": "peer_v1"},
        claim_scope_agent_id=agent_id,
    )
    packet = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=agent_id,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=registry_path,
        runtime_root=runtime_root,
        route_source="loopx_turn_plan",
        turn_instance_id="managed-current-turn",
        scheduler_execution_context={
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    )

    assert packet["effective_action"] != "unsettled_host_turn_recovery"
    assert packet["selected_todo"]["todo_id"] == "todo_visible"


def test_action_selection_route_binding_fails_closed_on_malformed_prefix(
    tmp_path: Path,
) -> None:
    payload = {
        "interaction_contract": {
            "cli_channel": {
                "selection_required": True,
                "selection_command": {"route_prefix": "loopx --runtime-root /tmp"},
            }
        }
    }

    bind_action_selection_cli_routes(
        payload,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )

    assert payload["interaction_contract"]["cli_channel"]["selection_command"][
        "route_prefix"
    ] == "loopx --runtime-root /tmp"


def test_turn_start_read_is_required_before_ordinary_work(tmp_path: Path) -> None:
    status = _ordinary_status_payload()
    baseline = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    packet = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        turn_start_hook_dispatch=_turn_start_dispatch(
            commands=(
                f"loopx --registry {tmp_path / 'registry.json'} "
                f"lark-inbox drain --goal-id {GOAL_ID}",
            )
        ),
    )

    required_reads = packet["interaction_contract"]["agent_channel"]["required_reads"]
    assert "required_reads" not in packet["interaction_contract"]["cli_channel"]
    assert required_reads == [
        {
            "kind": "operator_inbox",
            "command": (
                f"loopx --registry {tmp_path / 'registry.json'} "
                f"lark-inbox drain --goal-id {GOAL_ID}"
            ),
            "reason": "turn-start hook synchronized new operator inbox evidence",
            "source": "turn_start_capability_hook",
            "ordering": "before_work",
            "hook_id": "operator_inbox.turn_start_sync_0",
            "capability_id": "operator-inbox",
        }
    ]
    assert packet["interaction_contract"]["user_channel"]["notify"] == "NOTIFY"
    assert packet["interaction_contract"]["user_channel"]["action_required"] is False
    assert packet.get("selected_todo") == baseline.get("selected_todo")
    assert (
        packet["agent_todo_summary"]["first_executable_items"]
        == baseline["agent_todo_summary"]["first_executable_items"]
    )
    assert packet["recommended_action"] == baseline["recommended_action"]
    assert packet["effective_action"] == baseline["effective_action"] == "normal_run"


def test_fresh_turn_start_read_notifies_without_preempting_selected_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.quota.goal_boundary.operator_inbox_binding",
        lambda **_kwargs: {
            "status": "verified",
            "attention_required": False,
        },
    )
    status = _status_with_inbox(tmp_path)
    baseline = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    packet = build_live_quota_should_run_decision(
        status,
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        operator_inbox_urgency_projector=_material_review_urgency,
        turn_start_hook_dispatch=_turn_start_dispatch(),
    )

    assert packet["effective_action"] == baseline["effective_action"] == "normal_run"
    assert packet.get("selected_todo") == baseline.get("selected_todo")
    assert packet["recommended_action"] == baseline["recommended_action"]
    assert packet["interaction_contract"]["user_channel"]["notify"] == "NOTIFY"
    assert packet["interaction_contract"]["user_channel"]["action_required"] is False
    assert packet["interaction_contract"]["agent_channel"]["required_reads"]


def test_unsettled_inbox_material_preempts_on_following_turn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.quota.goal_boundary.operator_inbox_binding",
        lambda **_kwargs: {
            "status": "verified",
            "attention_required": False,
        },
    )
    packet = build_live_quota_should_run_decision(
        _status_with_inbox(tmp_path),
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        operator_inbox_urgency_projector=_material_review_urgency,
        turn_start_hook_dispatch=_turn_start_dispatch(required=False),
    )

    assert packet["effective_action"] == "operator_inbox_material_review_due"
    assert packet["work_lane_contract"]["priority_preemption"] is True
    assert packet["interaction_contract"]["agent_channel"]["required_reads"] == []


def test_fresh_direct_reply_still_preempts_selected_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.quota.goal_boundary.operator_inbox_binding",
        lambda **_kwargs: {
            "status": "verified",
            "attention_required": False,
        },
    )
    packet = build_live_quota_should_run_decision(
        _status_with_inbox(tmp_path),
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        operator_inbox_urgency_projector=(
            lambda **kwargs: _material_review_urgency(reply_due=True, **kwargs)
        ),
        turn_start_hook_dispatch=_turn_start_dispatch(),
    )

    assert packet["effective_action"] == "lark_inbox_reply_due"
    assert packet["work_lane_contract"]["priority_preemption"] is True
    assert packet["interaction_contract"]["agent_channel"]["required_reads"]


def test_fresh_read_does_not_hide_older_unsettled_material(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.quota.goal_boundary.operator_inbox_binding",
        lambda **_kwargs: {
            "status": "verified",
            "attention_required": False,
        },
    )
    packet = build_live_quota_should_run_decision(
        _status_with_inbox(tmp_path),
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        operator_inbox_urgency_projector=(
            lambda **kwargs: _material_review_urgency(
                pending_count=2,
                **kwargs,
            )
        ),
        turn_start_hook_dispatch=_turn_start_dispatch(observation_count=1),
    )

    assert packet["effective_action"] == "operator_inbox_material_review_due"
    assert packet["work_lane_contract"]["priority_preemption"] is True
    assert packet["interaction_contract"]["agent_channel"]["required_reads"]


def test_non_inbox_hook_observations_do_not_mask_unsettled_inbox_material(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.quota.goal_boundary.operator_inbox_binding",
        lambda **_kwargs: {
            "status": "verified",
            "attention_required": False,
        },
    )
    dispatch = _turn_start_dispatch(observation_count=1)
    dispatch["results"].append(
        {
            "hook_id": "repository.turn_start_sync",
            "capability_id": "repository",
            "agent_read_required": True,
            "observation_count": 10,
        }
    )
    dispatch["required_reads"].append(
        {
            "kind": "repository",
            "command": "git status --short",
            "reason": "read repository state",
            "ordering": "before_work",
            "source": "turn_start_capability_hook",
            "hook_id": "repository.turn_start_sync",
            "capability_id": "repository",
        }
    )

    packet = build_live_quota_should_run_decision(
        _status_with_inbox(tmp_path),
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        operator_inbox_urgency_projector=(
            lambda **kwargs: _material_review_urgency(
                pending_count=2,
                **kwargs,
            )
        ),
        turn_start_hook_dispatch=dispatch,
    )

    assert packet["effective_action"] == "operator_inbox_material_review_due"
    assert packet["work_lane_contract"]["priority_preemption"] is True


def test_turn_start_read_is_not_projected_for_empty_or_failed_dispatch(
    tmp_path: Path,
) -> None:
    status = _ordinary_status_payload()
    dispatches = [
        _turn_start_dispatch(required=False),
        {
            "registered_count": 1,
            "invoked_count": 0,
            "results": [],
            "failures": [
                {
                    "hook_id": "lark.turn_start_inbox_sync",
                    "capability_id": "lark-event-inbox",
                    "error_code": "producer_failed",
                }
            ],
        },
    ]

    for dispatch in dispatches:
        packet = build_live_quota_should_run_decision(
            status,
            goal_id=GOAL_ID,
            agent_id=None,
            available_capabilities=["shell"],
            include_scheduler_detail=False,
            codex_app_current_rrule=None,
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime",
            turn_start_hook_dispatch=dispatch,
        )

        assert packet["interaction_contract"]["agent_channel"]["required_reads"] == []
        assert "required_reads" not in packet["interaction_contract"]["cli_channel"]


def test_duplicate_required_inbox_routes_project_one_public_safe_read(
    tmp_path: Path,
) -> None:
    command = "loopx lark-inbox drain --goal-id fixture"
    dispatch = _turn_start_dispatch(commands=(command, command))
    packet = build_live_quota_should_run_decision(
        _ordinary_status_payload(),
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        turn_start_hook_dispatch=dispatch,
    )

    reads = packet["interaction_contract"]["agent_channel"]["required_reads"]
    assert len(reads) == 1
    assert reads[0]["command"] == command
    assert "required_reads" not in packet


def test_prior_closeout_identity_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    """A conflicting prior receipt cannot be resolved into a recovery verdict."""

    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    agent_id = "codex-fixture"
    prior_turn_id = "managed-prior-turn"
    state_path.write_text(
        "# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Keep advancing the selected task.\n"
        "  <!-- loopx:todo todo_id=todo_ordinary_work status=open "
        "task_class=advancement_task -->\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path),
                        "state_file": str(state_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    goal_runtime = runtime_root / "goals" / GOAL_ID
    goal_runtime.mkdir(parents=True)
    conflicting = [
        {"todo_id": "todo_ordinary_work"},
        {"todo_id": "todo_other_work"},
    ]
    (goal_runtime / "rollout-event-log.jsonl").write_text(
        "".join(
            json.dumps(
                build_rollout_event(
                    goal_id=GOAL_ID,
                    event_kind="quota_should_run",
                    agent_id=agent_id,
                    todo_id=str(details["todo_id"]),
                    run_id=prior_turn_id,
                    status="normal_run",
                    summary="managed heartbeat guard requires closeout",
                    details={**details, "closeout_required": True},
                )
            )
            + "\n"
            for details in conflicting
        ),
        encoding="utf-8",
    )

    todo_text = "[P1] Keep advancing the selected task."
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": "todo_ordinary_work",
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
        coordination={
            "registered_agents": [agent_id],
            "agent_model": "peer_v1",
        },
        claim_scope_agent_id=agent_id,
    )
    with pytest.raises(HeartbeatReceiptIdentityConflictError):
        build_live_quota_should_run_decision(
            status,
            goal_id=GOAL_ID,
            agent_id=agent_id,
            available_capabilities=["shell"],
            include_scheduler_detail=False,
            codex_app_current_rrule=None,
            registry_path=registry_path,
            runtime_root=runtime_root,
            route_source="loopx_turn_plan",
            turn_instance_id="managed-current-turn",
            scheduler_execution_context={
                "host_surface": "generic_cli",
                "scheduler_owner": "agent_cli_loop",
                "execution_mode": "interactive",
            },
        )


@pytest.mark.parametrize("quota_state", ["eligible", "paused", "operator_gate", "exhausted"])
@pytest.mark.parametrize("required_reads", [False, True])
def test_current_quota_outputs_retire_packet_without_losing_signed_actions(
    tmp_path: Path, quota_state: str, required_reads: bool,
) -> None:
    from loopx.control_plane.quota.turn_envelope import (
        build_turn_envelope, quota_action_signature_document,
        turn_envelope_action_signature_document,
    )
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority

    status = quota_status_payload(
        goal_id=GOAL_ID, status="active", quota_state=quota_state,
        agent_todo_items=[{
            "todo_id": "todo_packet_retirement", "index": 1,
            "text": "[P1] Verify the bounded change", "role": "agent",
            "status": "open", "priority": "P1", "task_class": "advancement_task",
        }],
        recommended_action="Verify the bounded change",
    )
    payload = build_live_quota_should_run_decision(
        status, goal_id=GOAL_ID, agent_id=None, available_capabilities=["shell"],
        include_scheduler_detail=False, codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        turn_start_hook_dispatch=_turn_start_dispatch(required=required_reads),
    )
    assert "protocol_action_packet" not in payload
    envelope = build_turn_envelope(payload)
    assert "protocol_action_packet" not in envelope["contract_capsule"]
    assert quota_action_signature_document(payload) == turn_envelope_action_signature_document(envelope)
    assert extract_turn_authority({"turn_envelope": envelope})["primary_action"]
    if quota_state == "paused":
        assert payload["should_run"] is False
        assert envelope["writeback"]["spend_allowed_now"] is False
    if required_reads:
        assert payload["interaction_contract"]["agent_channel"]["required_reads"]


def test_packet_retirement_preserves_reads_and_independent_capability_command(tmp_path: Path) -> None:
    from loopx.control_plane.capability_hooks import (
        InteractionProjectionHookRegistration,
        INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    )
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority

    command = f"loopx periodic-report consume-pending --goal-id {GOAL_ID} --agent-id fixture-agent --execute"
    hook = InteractionProjectionHookRegistration(
        hook_id="periodic_report.pending_intent", capability_id="periodic-report",
        projection_slots=("pending_capability_intent",),
        requested_read_scope=("post_writeback_intent_journal",),
        producer=lambda: {
            "schema_version": INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "periodic_report.pending_intent", "capability_id": "periodic-report",
            "phase": "interaction_projection", "status": "candidate",
            "projection_slot": "pending_capability_intent",
            "payload": {
                "schema_version": "pending_capability_intent_projection_v0",
                "capability_id": "periodic-report", "intent_kind": "periodic_report.trigger_evaluation",
                "idempotency_key": "periodic-report:fixture", "intent_digest": "sha256:" + "a" * 64,
                "goal_id": GOAL_ID, "agent_id": "fixture-agent", "state": "pending",
                "action_kind": "consume_periodic_report_intent", "action_summary": "Generate the exact report.",
                "command": command, "generation_authorized": True,
                "external_delivery_authorized": True, "agent_read_required": True,
            },
        },
    )
    payload = build_live_quota_should_run_decision(
        _ordinary_status_payload(), goal_id=GOAL_ID, agent_id=None,
        available_capabilities=["shell"], include_scheduler_detail=False,
        codex_app_current_rrule=None, registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime", interaction_projection_hooks=[hook],
        turn_start_hook_dispatch=_turn_start_dispatch(),
    )
    assert "protocol_action_packet" not in payload
    assert payload["effective_action"] == "governed_capability_intent"
    assert payload["normal_delivery_allowed"] is False
    contract = payload["interaction_contract"]
    assert contract["cli_channel"]["next_cli_actions"] == [command]
    assert contract["agent_channel"]["required_reads"]
    envelope = build_turn_envelope(payload)
    assert "protocol_action_packet" not in envelope["contract_capsule"]
    authority = extract_turn_authority({"turn_envelope": envelope})
    assert authority["primary_action"] == "Generate the exact report."
    assert envelope["writeback"]["next_cli_actions"] == [command]
    assert authority["required_reads"]


def test_retained_selection_reentry_stays_packet_free_and_signed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from loopx.control_plane.quota.turn_envelope import (
        build_turn_envelope,
        quota_action_signature_document,
        turn_envelope_action_signature_document,
    )
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority

    status_payload = _ordinary_status_payload()
    status_item = status_payload["attention_queue"]["items"][0]

    from loopx.control_plane.todos import list_readback

    monkeypatch.setattr(
        list_readback,
        "list_goal_todos",
        lambda **_kwargs: {
            "ok": True,
            "todos": status_item["agent_todos"]["first_open_items"],
            "agent_todos": status_item["agent_todos"],
            "user_todos": status_item["user_todos"],
        },
    )

    payload = build_live_quota_should_run_decision(
        status_payload,
        goal_id=GOAL_ID,
        agent_id=None,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        retained_action_selection_todo_id="todo_retained_explicit",
        turn_instance_id="turn-retained-packet-retirement",
    )

    assert payload["retained_action_selection"]["disposition"] == (
        "require_explicit_selection"
    )
    assert payload["state"] == "action_selection_required"
    assert payload["should_run"] is False
    assert "selected_todo" not in payload
    assert "protocol_action_packet" not in payload
    interaction = payload["interaction_contract"]
    assert interaction["mode"] == "skip"
    assert interaction["agent_channel"]["must_attempt"] is False
    assert interaction["agent_channel"]["delivery_allowed"] is False

    envelope = build_turn_envelope(payload)
    assert "protocol_action_packet" not in envelope["contract_capsule"]
    assert quota_action_signature_document(payload) == (
        turn_envelope_action_signature_document(envelope)
    )
    authority = extract_turn_authority({"turn_envelope": envelope})
    assert authority["primary_action"] == interaction["agent_channel"][
        "primary_action"
    ]
    assert authority["write_scope"] == []
    assert envelope["writeback"]["spend_allowed_now"] is False
    assert envelope["writeback"]["spend_after_validation"] is False


@pytest.mark.parametrize(
    ("advancement_count", "requires_replan"),
    [(1, False), (14, False), (15, True)],
)
def test_retained_selection_reentry_refreshes_provider_todos_before_replan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    advancement_count: int,
    requires_replan: bool,
) -> None:
    """Refresh retained choices before applying the claimed advancement threshold."""

    agent_id = "agent-provider-frontier"
    selected_todo_id = "todo_provider_selected"
    stale_status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "todo_id": selected_todo_id,
                "index": 1,
                "text": "[P1] Continue the selected delivery.",
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
                "claimed_by": agent_id,
                "updated_at": "2026-09-21T00:00:00Z",
            }
        ],
        recommended_action="[P1] Continue the selected delivery.",
        next_action="[P1] Continue the selected delivery.",
        claim_scope_agent_id=agent_id,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [agent_id],
        },
    )
    fresh_items = [
        {
            "todo_id": selected_todo_id if index == 0 else f"todo_provider_{index:012d}",
            "index": index + 1,
            "text": (
                "[P1] Continue the selected delivery."
                if index == 0
                else f"[P1] Provider frontier item {index}."
            ),
            "role": "agent",
            "status": "open",
            "priority": "P1",
            "task_class": (
                "advancement_task" if index < advancement_count else "blocker"
            ),
            "claimed_by": agent_id,
            "updated_at": f"2026-09-{(index % 9) + 1:02d}T00:00:00Z",
        }
        for index in range(20)
    ]
    from loopx.control_plane.testing.quota_fixtures import quota_todo_summary

    fresh_agent_todos = quota_todo_summary(
        fresh_items,
        role="agent",
        claim_scope_agent_id=agent_id,
    )
    fresh_user_todos = quota_todo_summary([], role="user")
    reads: list[str] = []

    def fresh_todos(**_kwargs: object) -> dict[str, object]:
        reads.append("provider")
        return {
            "ok": True,
            "todos": fresh_items,
            "agent_todos": fresh_agent_todos,
            "user_todos": fresh_user_todos,
        }

    from loopx.control_plane.todos import list_readback

    monkeypatch.setattr(list_readback, "list_goal_todos", fresh_todos)

    payload = build_live_quota_should_run_decision(
        stale_status,
        goal_id=GOAL_ID,
        agent_id=agent_id,
        available_capabilities=["shell"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        retained_action_selection_todo_id=selected_todo_id,
        turn_instance_id="turn-provider-frontier-reentry",
    )

    assert reads == ["provider"]
    assert payload["selected_todo"]["todo_id"] == selected_todo_id
    assert payload["retained_action_selection"]["disposition"] == (
        "preserve_retained_todo"
    )
    assert payload["interaction_contract"]["agent_channel"]["must_attempt"] is True
    if requires_replan:
        assert payload["decision"] == "autonomous_replan_required"
        assert payload["replan_action_packet"]["obligation_id"]
    else:
        assert payload["decision"] == "run"
        assert not payload.get("replan_action_packet")
