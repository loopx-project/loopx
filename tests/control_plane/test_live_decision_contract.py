from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from loopx.control_plane.quota import (
    live_decision,
    live_decision_replan,
    live_decision_routes,
    live_decision_turn_reads,
)
from loopx.control_plane.quota.effect_program import (
    ReceiptBoundReplayPhase,
    SettlementIdentity,
    SettlementResult,
)
from loopx.control_plane.quota.effective_action import EffectiveAction
from loopx.control_plane.quota.live_decision_contracts import (
    non_negative_int,
    require_mapping,
)
from loopx.control_plane.quota.settlement import QuotaSettlementReadback

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIVE_DECISION_MODULES = (
    "loopx/control_plane/quota/live_decision.py",
    "loopx/control_plane/quota/live_decision_contracts.py",
    "loopx/control_plane/quota/live_decision_routes.py",
    "loopx/control_plane/quota/live_decision_turn_reads.py",
    "loopx/control_plane/quota/live_decision_recovery.py",
    "loopx/control_plane/quota/live_decision_replan.py",
)


def _write_registry(
    tmp_path: Path,
    *,
    goal_id: str = "g1",
    extra_goal: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    repo_root = tmp_path / "repo"
    runtime_root.mkdir(parents=True, exist_ok=True)
    repo_root.mkdir(parents=True, exist_ok=True)
    state_file = repo_root / "TODOS.md"
    state_file.write_text("# Todos\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    goal: dict[str, object] = {
        "id": goal_id,
        "goal_instance_id": "gi-1",
        "repo": str(repo_root),
        "state_file": "TODOS.md",
        "enabled": True,
        "registered_agents": ["agent-1"],
        **(extra_goal or {}),
    }
    registry_path.write_text(json.dumps({"goals": [goal]}), encoding="utf-8")
    return registry_path, runtime_root


def _make_readback(
    *,
    replay_phase: ReceiptBoundReplayPhase | None = None,
    heartbeat_receipt: dict[str, object] | None = None,
    admission_recorded_at: str | None = None,
    admission_event_id: str | None = None,
    semantic_replan_guard: dict[str, object] | None = None,
    progress: dict[str, object] | None = None,
) -> QuotaSettlementReadback:
    identity = SettlementIdentity(
        goal_id="g1",
        agent_id="agent-1",
        todo_id=None,
        turn_instance_id="turn-1",
        replan_obligation_id="ob-1",
    )
    none_result: SettlementResult[dict[str, object]] = SettlementResult(value=None)
    return QuotaSettlementReadback(
        identity=SettlementResult.pure(identity),
        writeback=none_result,
        spend=none_result,
        delivery=none_result,
        settlement=none_result,
        terminal_closeout=none_result,
        terminal_settlement=none_result,
        workspace_causality=None,
        semantic_replan_guard=semantic_replan_guard,
        writeback_run=None,
        spend_run=None,
        heartbeat_receipt=heartbeat_receipt,
        writeback_event=None,
        spend_event=None,
        completion_event=None,
        monitor_phase=None,
        replay_phase=replay_phase,
        progress=progress,
        admission_recorded_at=admission_recorded_at,
        admission_event_id=admission_event_id,
    )


def test_live_decision_module_layout_and_zero_any_budgets() -> None:
    test_file = Path(__file__).resolve()
    for rel_path in [*_LIVE_DECISION_MODULES, str(test_file.relative_to(_REPO_ROOT))]:
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source, filename=rel_path)
        any_nodes = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        suppressions = [
            idx
            for idx, line in enumerate(lines, start=1)
            if ("type:" + " ignore") in line or ("no" + "qa") in line
        ]
        assert not any_nodes, f"{rel_path} still references Any at lines {any_nodes}"
        assert not suppressions, f"{rel_path} has suppressions at {suppressions}"
        if rel_path.endswith("live_decision.py"):
            assert len(lines) <= 650, f"{rel_path} exceeded 650 LOC ({len(lines)})"
        elif rel_path != str(test_file.relative_to(_REPO_ROOT)):
            assert len(lines) < 750, f"{rel_path} exceeded 750 LOC ({len(lines)})"

    live_source = (_REPO_ROOT / "loopx/control_plane/quota/live_decision.py").read_text(
        encoding="utf-8"
    )
    assert live_source.count("work_lane_contract_v1") == 1
    for helper_rel in _LIVE_DECISION_MODULES[1:]:
        helper_src = (_REPO_ROOT / helper_rel).read_text(encoding="utf-8")
        assert "work_lane_contract_v1" not in helper_src


def test_live_decision_exports_and_monkeypatch_seams() -> None:
    expected = (
        "EffectiveAction",
        "HostObservationResolver",
        "BoundedResearchFrontierProjector",
        "ProgressReviewContextReader",
        "QuotaSettlementReadback",
        "registered_agent_ids_for_goal",
        "read_heartbeat_settlement",
        "apply_unsettled_host_turn_recovery_if_required",
        "apply_receipt_bound_wait_recovery",
        "attach_settlement_progress",
        "bind_scheduler_followup_cli_routes",
        "bind_action_selection_cli_routes",
        "_apply_pending_capability_intent_precedence",
        "_build_remote_authority_wait_decision",
        "_receipt_bound_guard_fields",
        "_historical_ack_writeback_required",
        "_prepared_execution_needs_readback",
        "_original_replan_settlement_required",
        "_project_receipt_replan_settlement",
        "_apply_retained_action_selection_reentry",
        "_fresh_read_covers_all_pending_material",
        "_turn_start_required_reads",
        "_project_turn_start_required_reads",
        "_fresh_operator_inbox_observation_count",
        "_fresh_operator_inbox_read_required",
        "_bind_followup_turn_argument",
        "_scheduler_fallback_launcher",
        "_receipt_bound_recovery_todo",
        "_project_unbound_work_fields",
        "_restore_replan_obligation_without_foreign_acks",
        "build_live_quota_should_run_decision",
    )
    for name in expected:
        assert hasattr(live_decision, name), f"Missing exported symbol {name}"


def test_remote_authority_path_honors_monkeypatched_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path, runtime_root = _write_registry(
        tmp_path,
        extra_goal={
            "control_plane": {
                "coordination": {
                    "remote_authority_gateway": {
                        "enabled": True,
                        "base_url": "https://example.invalid",
                    }
                }
            }
        },
    )
    calls: list[str] = []

    def fake_read_settlement(
        runtime: Path,
        *,
        goal_id: str,
        agent_id: str | None,
        todo_id: str | None,
        turn_instance_id: str | None,
        replan_obligation_id: str | None = None,
        registry_path: Path | None = None,
        goal_ref: object = None,
        tolerate_malformed_history: bool = False,
    ) -> QuotaSettlementReadback | None:
        calls.append(f"read:{turn_instance_id}")
        return None

    def fake_recovery(
        payload: dict[str, object],
        **kwargs: object,
    ) -> bool:
        calls.append(f"recover:{kwargs.get('current_turn_instance_id')}")
        payload.update(
            decision="wait",
            state="waiting",
            effective_action=EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value,
        )
        return True

    monkeypatch.setattr(live_decision, "read_heartbeat_settlement", fake_read_settlement)
    monkeypatch.setattr(
        live_decision,
        "apply_unsettled_host_turn_recovery_if_required",
        fake_recovery,
    )

    from loopx.control_plane.coordination import remote_authority_gateway as gateway_mod

    monkeypatch.setattr(
        gateway_mod,
        "remote_authority_config",
        lambda goal: {"enabled": True, "base_url": "https://example.invalid"},
    )
    monkeypatch.setattr(
        "loopx.quota._build_quota_plan_for_goal",
        lambda status_payload, goal_id: ({"ok": True, "items": []}, True),
    )

    payload = live_decision.build_live_quota_should_run_decision(
        {"attention_queue": {"items": []}},
        goal_id="g1",
        agent_id="agent-1",
        available_capabilities=None,
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=registry_path,
        runtime_root=runtime_root,
        turn_instance_id="turn-remote-1",
    )
    assert calls == ["read:turn-remote-1", "recover:turn-remote-1"]
    assert payload["effective_action"] == EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value


def test_route_binding_key_presence_and_single_resolve_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path, runtime_root = _write_registry(tmp_path)
    monkeypatch.setattr(
        live_decision_routes,
        "_scheduler_fallback_launcher",
        lambda: ["loopx-apply-rrule"],
    )

    empty_payload: dict[str, object] = {"scheduler_hint": {"app_automation": {}}}
    live_decision.bind_scheduler_followup_cli_routes(
        empty_payload, registry_path=registry_path, runtime_root=runtime_root
    )
    assert empty_payload == {"scheduler_hint": {"app_automation": {}}}

    resolve_calls: list[Path] = []
    original_resolve = Path.resolve

    def tracked_resolve(self: Path, strict: bool = False) -> Path:
        resolve_calls.append(self)
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", tracked_resolve)

    ack_args: dict[str, object] = {}
    fallback_args_absent: dict[str, object] = {"keep": True}
    fallback_hint: dict[str, object] = {
        "cli_args": [
            "loopx-apply-rrule",
            "--turn-instance-id",
            "${LOOPX_TURN_INSTANCE_ID}",
        ],
        "args": fallback_args_absent,
    }
    ack_hint: dict[str, object] = {
        "cli_args": ["loopx", "quota", "scheduler-ack", "--execute"],
        "args": ack_args,
    }
    failure_hint: dict[str, object] = {
        "cli_args": ["loopx", "quota", "scheduler-failure", "--execute"],
    }
    payload: dict[str, object] = {
        "scheduler_hint": {
            "app_automation": {
                "ack_hint": ack_hint,
                "failure_hint": failure_hint,
                "fallback_hint": fallback_hint,
            }
        }
    }
    live_decision.bind_scheduler_followup_cli_routes(
        payload,
        registry_path=registry_path,
        runtime_root=runtime_root,
        turn_instance_id="turn-42",
    )
    assert len(resolve_calls) == 2
    assert ack_args["turn_instance_id"] == "turn-42"
    assert "turn_instance_id" not in fallback_args_absent

    fallback_args_present: dict[str, object] = {
        "turn_instance_id": "${LOOPX_TURN_INSTANCE_ID}"
    }
    payload2: dict[str, object] = {
        "scheduler_hint": {
            "app_automation": {
                "fallback_hint": {
                    "cli_args": [
                        "loopx-apply-rrule",
                        "--turn-instance-id",
                        "${LOOPX_TURN_INSTANCE_ID}",
                    ],
                    "args": fallback_args_present,
                }
            }
        }
    }
    live_decision.bind_scheduler_followup_cli_routes(
        payload2,
        registry_path=registry_path,
        runtime_root=runtime_root,
        turn_instance_id="turn-99",
    )
    assert fallback_args_present["turn_instance_id"] == "turn-99"

    resolve_calls.clear()
    no_select: dict[str, object] = {
        "interaction_contract": {"cli_channel": {"selection_required": False}}
    }
    live_decision.bind_action_selection_cli_routes(
        no_select, registry_path=registry_path, runtime_root=runtime_root
    )
    assert resolve_calls == []

    bad_quotes_cmd: dict[str, object] = {"route_prefix": "loopx 'unclosed"}
    live_decision.bind_action_selection_cli_routes(
        {
            "interaction_contract": {
                "cli_channel": {
                    "selection_required": True,
                    "selection_command": bad_quotes_cmd,
                }
            }
        },
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    assert bad_quotes_cmd["route_prefix"] == "loopx 'unclosed"

    selection_cmd: dict[str, object] = {
        "route_prefix": "loopx quota should-run --format json --goal-id g1"
    }
    live_decision.bind_action_selection_cli_routes(
        {
            "interaction_contract": {
                "cli_channel": {
                    "selection_required": True,
                    "selection_command": selection_cmd,
                }
            }
        },
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    route_prefix = str(selection_cmd["route_prefix"])
    assert "--registry" in route_prefix and "--runtime-root" in route_prefix
    assert route_prefix.index("--runtime-root") < route_prefix.index("--format")


def test_single_inbox_scan_in_live_decision_and_guard_empty_string_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path, runtime_root = _write_registry(tmp_path)
    scan_calls: list[object] = []
    real_scan = live_decision_turn_reads._fresh_operator_inbox_observation_count

    def counted_scan(dispatch: object) -> int:
        scan_calls.append(dispatch)
        assert isinstance(dispatch, dict) or dispatch is None
        return real_scan(dispatch)

    monkeypatch.setattr(
        live_decision, "_fresh_operator_inbox_observation_count", counted_scan
    )
    captured: dict[str, object] = {}

    def fake_build_quota_should_run(
        status_payload: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        captured["pre_scans"] = len(scan_calls)
        captured["projector"] = kwargs["operator_inbox_urgency_projector"]
        return {
            "ok": True,
            "decision": "run",
            "should_run": True,
            "state": "eligible",
            "effective_action": EffectiveAction.NORMAL_RUN.value,
        }

    monkeypatch.setattr(live_decision, "build_quota_should_run", fake_build_quota_should_run)
    monkeypatch.setattr(
        live_decision,
        "build_interaction_contract",
        lambda *args, **kwargs: {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {},
            "agent_channel": {},
            "cli_channel": {},
        },
    )

    hook_dispatch = {
        "required_reads": [{"kind": "operator_inbox", "hook_id": "h-inbox"}],
        "results": [{"hook_id": "h-inbox", "agent_read_required": True, "observation_count": 2}],
    }
    live_decision.build_live_quota_should_run_decision(
        {"attention_queue": {"items": []}},
        goal_id="g1",
        agent_id="agent-1",
        available_capabilities=None,
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=registry_path,
        runtime_root=runtime_root,
        turn_start_hook_dispatch=hook_dispatch,
        operator_inbox_urgency_projector=lambda **_: {
            "pending_count": 2,
            "material_review_due": True,
        },
    )
    assert captured["pre_scans"] == 1
    assert len(scan_calls) == 1
    projector = captured["projector"]
    assert callable(projector)
    assert projector() == {"pending_count": 2, "material_review_due": False}

    readback = _make_readback(
        heartbeat_receipt={"recorded_at": "2026-11-26T00:00:00Z"},
        admission_recorded_at="",
        admission_event_id="ev-1",
        semantic_replan_guard={"scope": "turn_guard", "selected_obligation_id": "ob-1"},
    )
    fields = live_decision._receipt_bound_guard_fields(readback, "ob-1")
    assert fields["receipt_bound_guard_recorded_at"] == "2026-11-26T00:00:00Z"


def test_malformed_inputs_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path, runtime_root = _write_registry(tmp_path)

    with pytest.raises(RuntimeError, match="malformed"):
        live_decision._apply_retained_action_selection_reentry(
            {"effective_action": "normal_run", "selected_todo": {"todo_id": "todo_alpha_1"}},
            retained_todo_id="todo_alpha_1",
            available_capabilities=None,
            scheduler_execution_context=None,
            turn_instance_id=None,
            registry_path=registry_path,
            runtime_root=runtime_root,
            reconcile_retained_fn=lambda **_: {"clear_fields": "bad"},
        )

    with pytest.raises(RuntimeError, match="unsupported"):
        live_decision._apply_retained_action_selection_reentry(
            {"effective_action": "normal_run", "selected_todo": {"todo_id": "todo_alpha_1"}},
            retained_todo_id="todo_alpha_1",
            available_capabilities=None,
            scheduler_execution_context=None,
            turn_instance_id=None,
            registry_path=registry_path,
            runtime_root=runtime_root,
            reconcile_retained_fn=lambda **_: {
                "disposition": "unexpected_disposition",
                "clear_fields": [],
            },
        )

    with pytest.raises(TypeError):
        live_decision._project_unbound_work_fields(
            {
                "interaction_contract": {
                    "cli_channel": {
                        "turn_binding_required": True,
                        "spend_policy": "blocked",
                        "next_cli_actions": "not-a-list",
                    }
                }
            }
        )

    monkeypatch.setattr(
        "loopx.control_plane.todos.list_readback.list_goal_todos",
        lambda **_: {"user_todos": [], "agent_todos": []},
    )
    with pytest.raises(TypeError):
        live_decision_replan.attach_goal_todos_to_attention_queue(
            {"attention_queue": "malformed"},
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="g1",
        )
    with pytest.raises(TypeError):
        live_decision_replan.attach_goal_todos_to_attention_queue(
            {"attention_queue": {"items": ["not-a-mapping"]}},
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="g1",
        )

    with pytest.raises(TypeError):
        require_mapping("not-a-mapping", label="packet")

    readback = _make_readback(
        replay_phase=ReceiptBoundReplayPhase.SETTLEMENT_PENDING,
        heartbeat_receipt={},
        progress={
            "schema_version": "quota_settlement_progress_v0",
            "checkpoint_recovery_required": True,
        },
    )
    with pytest.raises(TypeError):
        live_decision._project_receipt_replan_settlement(
            {
                "should_run": True,
                "effective_action": EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value,
                "replan_action_packet": {
                    "settlement_only": True,
                    "decision": ReceiptBoundReplayPhase.SETTLEMENT_PENDING.value,
                    "obligation_id": "ob-1",
                },
                "autonomous_replan_obligation": "malformed",
            },
            readback,
            available_capabilities=None,
            scheduler_execution_context=None,
            turn_instance_id="turn-1",
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_ref=None,
            attach_settlement_fn=lambda *args, **kwargs: None,
        )

    assert non_negative_int(None) == 0
    assert non_negative_int(False) == 0
    assert non_negative_int(-3) == 0
    assert non_negative_int("7") == 7
    with pytest.raises(ValueError):
        non_negative_int("not-an-int")
    with pytest.raises(TypeError):
        non_negative_int(object())
