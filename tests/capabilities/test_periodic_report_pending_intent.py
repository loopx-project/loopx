from __future__ import annotations

import json
import os
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.capabilities.periodic_report.pending_intent import (
    _atomic_write_text,
    _periodic_report_delivery_binding_ref,
    consume_pending_periodic_report_intent,
    pending_periodic_report_intents,
    periodic_report_pending_intent_interaction_hook,
)
from loopx.capabilities.periodic_report.request_action import (
    PeriodicReportRequestAdapter,
    PeriodicReportRequestPorts,
    SOURCE_BINDING_RECEIPT_SCHEMA,
    SOURCE_SETTLEMENT_RECEIPT_SCHEMA,
    SOURCE_SETTLEMENT_TERMINAL_STATUS,
    periodic_report_request_intents,
    record_periodic_report_request,
    settle_periodic_report_request,
)
from loopx.capabilities.periodic_report.incremental import (
    build_periodic_report_publication_candidate,
    commit_periodic_report_publication_cursor,
    periodic_report_incremental_baseline,
)
from loopx.capabilities.periodic_report.project_progress_snapshot import (
    build_project_progress_snapshot,
)
from loopx.todos import add_goal_todo
from loopx.status import collect_status, parse_active_state_todos
from loopx.quota import build_quota_should_run
from loopx.control_plane.capability_hooks import dispatch_interaction_projection_hooks
from loopx.control_plane.quota.live_decision import (
    _apply_pending_capability_intent_precedence,
)


GOAL_ID = "report-goal"
AGENT_ID = "report-agent"


def _calendar_fixture(tmp_path):
    registry, runtime = _fixture(tmp_path)
    for path in (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json"):
        path.unlink()
    _enable_calendar(registry)
    return registry, runtime


def _enable_calendar(registry):
    config = json.loads(registry.read_text())
    config["goals"][0]["control_plane"]["periodic_report"]["schedule"] = {
        "schema_version": "periodic_report_schedule_v0", "schedule_id": "weekly-report",
        "rrule": "FREQ=WEEKLY;BYDAY=FR;BYHOUR=18", "timezone": "Asia/Shanghai",
    }
    registry.write_text(json.dumps(config))


def test_calendar_actual_cli_preserves_capability_action_without_host(tmp_path, capsys, monkeypatch):
    from loopx.cli import main
    from loopx.control_plane.turn_driver.loop_controller import decide_loop_disposition

    registry, runtime = _calendar_fixture(tmp_path)

    def no_host(*args, **kwargs):
        pytest.fail("capability action must not invoke a normal host transaction")

    monkeypatch.setattr(
        "loopx.cli_commands.turn_run_once.execute_turn_run_once",
        no_host,
    )
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    scope = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--scan-path", str(tmp_path)]
    with monkeypatch.context() as read_only:
        read_only.setattr("loopx.cli_commands.lark_inbox.dispatch_goal_lark_turn_start_hooks", no_host)
        read_only.setattr("loopx.capabilities.periodic_report.cadence_runtime.extend_cadence_turn_start_dispatch", no_host)
        main([*prefix, "turn", "plan", *scope, "--host", "generic-cli",
              "--execution-mode", "isolated-headless"])
        cold_plan = json.loads(capsys.readouterr().out)
        assert cold_plan["effects"] == {"host_invoked": False, "state_written": False,
                                        "scheduler_acknowledged": False, "quota_spent": False}
        assert cold_plan["boundary"]["read_only"] is True
        assert not (runtime / "goals" / GOAL_ID / "periodic_reports").exists()
    main([*prefix, "turn", "run-once", *scope, "--host", "generic-cli",
          "--execution-mode", "isolated-headless", "--project", str(registry.parent), "--execute"])
    admitted = json.loads(capsys.readouterr().out)
    assert admitted["route"]["kind"] == "capability_action_required"
    assert admitted["effects"]["state_written"] is True
    assert admitted["effects"]["host_invoked"] is False
    assert admitted["capability_action"]["executed"] is False
    main([*prefix, "quota", "should-run", *scope])
    quota = json.loads(capsys.readouterr().out)
    command = quota["pending_capability_intent"]["command"]
    for action, execute, host in [("plan", False, "generic-cli"),
        ("run-once", False, "generic-cli"), ("run-once", True, "generic-cli"),
        ("run-once", True, "codex-cli")]:
        extras = ["--project", str(registry.parent)] if action == "run-once" else []
        code = main([*prefix, "turn", action, *scope, "--host", host,
                     "--execution-mode", "isolated-headless", *extras,
                     *(["--execute"] if execute else [])])
        turn = json.loads(capsys.readouterr().out)
        assert code == 0, {"action": action, "host": host, "error": turn.get("error"),
                          "route": turn.get("route"), "envelope": turn.get("turn_envelope")}
        assert turn["route"]["kind"] == "capability_action_required"
        assert turn["route"]["would_invoke_host"] is False
        assert turn["capability_action"]["intent"]["command"] == command
        assert turn["capability_action"]["command_argv"][:5] == ["loopx", "--registry", str(registry), "--runtime-root", str(runtime)]
        assert turn["capability_action"]["executed"] is False
        assert turn["turn_envelope"]["writeback"]["next_cli_actions"] == [command]
        assert turn["turn_envelope"]["replan_action_packet"] is None
        assert not any(turn["effects"].values())
        disposition = decide_loop_disposition(turn_receipt=None, quota_decision=turn["turn_envelope"])
        assert disposition["disposition"] == "capability_action_required"
        assert not disposition["launches_host"]
    assert not list((runtime / "goals" / GOAL_ID / "periodic_reports").glob("*/editorial_request.json"))


def test_calendar_hook_to_editorial_to_delivery_todo_preserves_window(tmp_path: Path) -> None:
    from datetime import datetime
    from loopx.capabilities.periodic_report.cadence_runtime import extend_cadence_turn_start_dispatch
    from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal

    registry, runtime = _calendar_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["registered_agents"].append("second-peer")
    registry.write_text(json.dumps(config))
    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                  now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"))
    assert extend_cadence_turn_start_dispatch({}, **kwargs, agent_id="second-peer")["invoked_count"] == 0
    dispatch = extend_cadence_turn_start_dispatch({}, **kwargs, agent_id=AGENT_ID)
    assert not dispatch["failures"]
    assert dispatch["invoked_count"] == 1
    assert dispatch["results"][0]["local_private_state_mutated"] is True
    config["goals"][0]["coordination"]["registered_agents"].reverse()
    registry.write_text(json.dumps(config))
    assert extend_cadence_turn_start_dispatch({}, **kwargs, agent_id="second-peer")["invoked_count"] == 0
    assert extend_cadence_turn_start_dispatch({}, **kwargs, agent_id=AGENT_ID)["results"][0]["agent_read_required"] is True
    # Removal is explicitly unavailable, never an empty/healthy projection or
    # a fresh generation under another peer's authority.
    frozen = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)
    config["goals"][0]["coordination"]["registered_agents"] = ["second-peer"]
    registry.write_text(json.dumps(config))
    removed = extend_cadence_turn_start_dispatch({}, **kwargs, agent_id="second-peer")
    assert removed["results"][0]["error_code"] == "cadence_pending_reporter_unavailable"
    assert removed["results"][0]["agent_read_required"] is False
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID) == frozen
    config["goals"][0]["coordination"]["registered_agents"].append(AGENT_ID)
    registry.write_text(json.dumps(config))
    required = consume_pending_periodic_report_intent(registry_path=registry,
        runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, execute=True)
    assert required["status"] == "editorial_required"
    request = json.loads(Path(required["editorial_request_path"]).read_text())
    assert request["actual_work_window"]["start_at"] == "2026-09-04T10:00:00Z"
    assert request["actual_work_window"]["end_at"] == "2026-09-11T10:00:00Z"
    assert "2026-09-04 18:00" in request["actual_work_window"]["period_label"]
    assert all(fact["status"] != "done" for fact in request["facts"])
    assert request["facts"][0]["status"] == "unknown"  # August completion is not this week's output.
    _write_editorial_response(required)
    ready = consume_pending_periodic_report_intent(registry_path=registry,
        runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, execute=True)
    assert ready["status"] == "delivery_ready"
    assert ready["external_writes_performed"] is False
    # Generation is not delivery. Do not overwrite the next week or produce
    # another consume request while the existing delivery Todo is outstanding.
    before = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)
    assert before["publication"] is None
    kwargs["now"] = datetime.fromisoformat("2026-09-18T10:15:00+00:00")
    repeated = extend_cadence_turn_start_dispatch({}, **kwargs, agent_id=AGENT_ID)
    assert not repeated["failures"]
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID) == before
    assert pending_periodic_report_intents(registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID) == []
    candidate = json.loads(Path(ready["artifacts"]["publication_candidate_path"]).read_text())
    commit_periodic_report_publication_cursor(runtime_root=runtime, candidate=candidate,
        publication_id="fixture-verified-publication", delivered_at="2026-09-11T10:05:00Z",
        covered_until="2026-09-11T10:00:00Z")
    kwargs["now"] = datetime.fromisoformat("2026-09-11T10:15:00+00:00")
    recovered = extend_cadence_turn_start_dispatch({}, **kwargs, agent_id=AGENT_ID)
    assert not recovered["failures"]
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)["publication"]["publication_id"] == "fixture-verified-publication"


@pytest.mark.parametrize("prepared", [False, True])
def test_calendar_reporter_replacement_uses_admitted_window_state(tmp_path, prepared):
    from datetime import datetime
    from loopx.capabilities.periodic_report.cadence_runtime import extend_cadence_turn_start_dispatch
    from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal

    registry, runtime = _calendar_fixture(tmp_path)
    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                  now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"))
    extend_cadence_turn_start_dispatch({}, **kwargs, agent_id=AGENT_ID)
    original = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)
    if prepared:
        assert consume_pending_periodic_report_intent(registry_path=registry,
            runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
            execute=True)["status"] == "editorial_required"
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["registered_agents"] = ["replacement-peer"]
    config["goals"][0]["control_plane"]["periodic_report"]["schedule"]["rrule"] = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=17"
    registry.write_text(json.dumps(config))
    dispatch = extend_cadence_turn_start_dispatch({}, **kwargs, agent_id="replacement-peer")
    assert not dispatch["failures"]
    result = dispatch["results"][0]
    current = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)
    if prepared:
        assert result["error_code"] == "cadence_pending_reporter_unavailable"
        assert result["agent_read_required"] is False
        assert current == original
    else:
        assert result["error_code"] is None
        assert result["agent_read_required"] is True
        assert current["supersedes"] == original["window"]["window_id"]
        assert current["window"]["agent_id"] == "replacement-peer"
        assert current["publication"] is None


@pytest.mark.parametrize("change", ["reporter", "schedule", "disabled", "stopped"])
def test_calendar_hook_rechecks_live_authority_after_construction(tmp_path, change):
    from datetime import datetime
    from loopx.control_plane.capability_hooks import dispatch_turn_start_hooks
    from loopx.capabilities.periodic_report.cadence_runtime import periodic_report_cadence_hooks
    from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal

    registry, runtime = _calendar_fixture(tmp_path)
    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                  now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"))
    hooks = periodic_report_cadence_hooks(**kwargs, agent_id=AGENT_ID)
    config = json.loads(registry.read_text())
    goal = config["goals"][0]
    if change == "reporter":
        goal["coordination"]["registered_agents"] = ["replacement-peer"]
    elif change == "schedule":
        goal["control_plane"]["periodic_report"]["schedule"]["rrule"] = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=17"
    elif change == "disabled":
        goal["control_plane"]["periodic_report"]["enabled"] = False
    else:
        goal["status"] = "stopped"
    registry.write_text(json.dumps(config))
    dispatched = dispatch_turn_start_hooks(hooks)
    assert not dispatched["failures"]
    journal = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)
    if change == "schedule":
        assert journal["window"]["due_at"] == "2026-09-11T09:00:00Z"
    else:
        assert journal is None
        assert dispatched["results"][0]["agent_read_required"] is False
    if change == "reporter":
        dispatch_turn_start_hooks(periodic_report_cadence_hooks(**kwargs, agent_id="replacement-peer"))
        assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)["window"]["agent_id"] == "replacement-peer"


@pytest.mark.parametrize("status", ["stopped", "paused", "archived"])
def test_inactive_goal_does_not_reactivate_an_admitted_report(tmp_path, status):
    from datetime import datetime
    from loopx.capabilities.periodic_report.cadence_runtime import extend_cadence_turn_start_dispatch
    from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal

    registry, runtime = _calendar_fixture(tmp_path)
    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID)
    extend_cadence_turn_start_dispatch({}, **kwargs, now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"))
    original = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)
    config = json.loads(registry.read_text())
    config["goals"][0]["status"] = status
    registry.write_text(json.dumps(config))
    assert pending_periodic_report_intents(**kwargs) == []
    assert consume_pending_periodic_report_intent(**kwargs, execute=True)["status"] == "no_pending_intent"
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID) == original


def test_canonically_stopped_goal_does_not_admit_periodic_report_window(tmp_path):
    from datetime import datetime
    from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal
    from loopx.capabilities.periodic_report.cadence_runtime import extend_cadence_turn_start_dispatch
    from loopx.capabilities.periodic_report.pending_intent import _active_delivery_subscription

    registry, runtime = _calendar_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["activation"] = {
        "schema_version": "loopx_goal_activation_v1", "state": "stopped"
    }
    registry.write_text(json.dumps(config))

    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID)
    dispatch = extend_cadence_turn_start_dispatch(
        {}, **kwargs, agent_id=AGENT_ID,
        now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"),
    )

    assert (
        dispatch["invoked_count"],
        read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID),
        _active_delivery_subscription(**kwargs),
    ) == (0, None, None)


def test_calendar_does_not_preempt_existing_stage_work(tmp_path):
    from datetime import datetime
    from loopx.capabilities.periodic_report.cadence_runtime import extend_cadence_turn_start_dispatch

    registry, runtime = _fixture(tmp_path)
    original = pending_periodic_report_intents(registry_path=registry,
        runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID)
    _enable_calendar(registry)
    extend_cadence_turn_start_dispatch({}, registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID, now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"))
    pending = pending_periodic_report_intents(registry_path=registry,
        runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert pending[:len(original)] == original
    assert "cadence_window" in pending[-1]["payload"]
    required = consume_pending_periodic_report_intent(registry_path=registry,
        runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, execute=True)
    request = json.loads(Path(required["editorial_request_path"]).read_text())
    assert request["completed_at"] == original[0]["payload"]["stage_completion"]["completed_at"]


def test_corrupt_calendar_preserves_other_intents_without_claiming_empty_health(tmp_path):
    from loopx.capabilities.periodic_report.cadence_journal import cadence_journal_path

    registry, runtime = _fixture(tmp_path)
    kwargs = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID)
    original = pending_periodic_report_intents(**kwargs)
    _enable_calendar(registry)
    path = cadence_journal_path(runtime, GOAL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken JSON")
    assert pending_periodic_report_intents(**kwargs) == original
    assert consume_pending_periodic_report_intent(**kwargs, execute=True)["status"] == "editorial_required"
    for sidecar in (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json"):
        sidecar.unlink()
    with pytest.raises(ValueError):
        pending_periodic_report_intents(**kwargs)
    assert path.read_text() == "broken JSON"
    config = json.loads(registry.read_text())
    config["goals"][0]["control_plane"]["periodic_report"].pop("schedule")
    registry.write_text(json.dumps(config))
    assert pending_periodic_report_intents(**kwargs) == []


def test_calendar_revision_change_serializes_with_editorial_preparation(tmp_path, monkeypatch):
    from datetime import datetime
    from threading import Event
    import loopx.capabilities.periodic_report.pending_intent as consumer
    from loopx.capabilities.periodic_report.cadence_journal import admit_cadence_window, read_cadence_journal
    from loopx.capabilities.periodic_report.machine_defaults import resolve_goal_periodic_report_subscription

    registry, runtime = _calendar_fixture(tmp_path)
    config = json.loads(registry.read_text())
    subscription = resolve_goal_periodic_report_subscription(config["goals"][0], None)
    kwargs = dict(runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
                  now=datetime.fromisoformat("2026-09-11T10:00:00+00:00"))
    frozen = admit_cadence_window(**kwargs, subscription=subscription)["window"]
    entered, release, attempted = Event(), Event(), Event()
    original_facts = consumer._progress_facts
    def paused_facts(**kwargs):
        entered.set()
        assert release.wait(3)
        return original_facts(**kwargs)
    monkeypatch.setattr(consumer, "_progress_facts", paused_facts)
    def change_configuration():
        attempted.set()
        return admit_cadence_window(**kwargs, subscription={**subscription,
            "effective_revision": "sha256:" + "b" * 64})
    with ThreadPoolExecutor(max_workers=2) as pool:
        consuming = pool.submit(consume_pending_periodic_report_intent,
            registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
            agent_id=AGENT_ID, execute=True)
        assert entered.wait(3)
        changing = pool.submit(change_configuration)
        assert attempted.wait(3)
        release.set()
        assert consuming.result(timeout=5)["status"] == "editorial_required"
        assert changing.result(timeout=5)["status"] == "configuration_changed"
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)["window"] == frozen


def test_delivery_binding_ref_is_valid_when_generation_digest_starts_with_digit() -> (
    None
):
    assert (
        _periodic_report_delivery_binding_ref(
            "report_generation_53429b77872cbe1130a3e2f3",
            {"effective_revision": "sha256:" + "a" * 64},
        )
        == "periodic-report:g53429b77872cbe11-aaaaaaaaaaaaa"
    )


def _intent() -> dict[str, object]:
    return {
        "schema_version": "loopx_capability_intent_v0",
        "intent_kind": "periodic_report.trigger_evaluation",
        "idempotency_key": "periodic-report:stage-example",
        "source_receipt_id": "pwr_example",
        "payload": {
            "schema_version": "periodic_report_trigger_evaluation_intent_v0",
            "stage_completion": {
                "schema_version": "periodic_report_stage_completion_receipt_v0",
                "stage_identity": "stage-example",
                "agent_id": AGENT_ID,
                "closed_vision_revision": "2026-08-30T09:00:00Z",
                "frontier_identity": "validated-goal-terminal",
                "transition": "goal_terminal",
                "completed_at": "2026-08-30T09:00:00Z",
                "acceptance": "validated",
                "outcome_checkpoint_satisfied": True,
                "durable_writeback_required": True,
                "evidence_refs": ["goal_terminal_state_v0"],
            },
            "profile_ref": {
                "profile_id": "weekly_progress",
                "profile_version": "v1",
                "profile_digest": "sha256:" + "1" * 64,
            },
            "trigger_policy": {
                "enabled_kinds": ["bounded_segment_milestone"],
                "minimum_interval_seconds": 0,
            },
            "generation_authorized": False,
            "external_delivery_authorized": False,
        },
        "requested_write_scope": [],
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        """---
status: active
---

# Active Goal State

## User Todo

## Agent Todo

- [x] Finish the bounded analysis.
  <!-- loopx:todo todo_id=todo_finished status=done task_class=advancement_task claimed_by=report-agent evidence=validated-analysis-outcome updated_at=2026-08-30T09:00:00Z -->
""",
        encoding="utf-8",
    )
    registry = project / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                        "control_plane": {
                            "periodic_report": {
                                "enabled": True,
                                "profile_preset": "weekly-progress",
                                "route_ref": "report-route",
                                "timezone": "Asia/Shanghai",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sidecar_dir = runtime / "goals" / GOAL_ID / "post_writeback_hooks"
    sidecar_dir.mkdir(parents=True)
    sidecar = sidecar_dir / ("pwh_" + "a" * 64 + ".json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "loopx_post_writeback_capability_hook_receipt_v0",
                "dispatch_id": sidecar.stem,
                "hook_id": "periodic_report.runtime_trigger",
                "capability_id": "periodic-report",
                "source_receipt_id": "pwr_example",
                "status": "intent_recorded",
                "intent": _intent(),
                "error_code": None,
                "attempt_count": 1,
                "recorded_at": "2026-08-30T09:00:01Z",
            }
        ),
        encoding="utf-8",
    )
    return registry, runtime


def _write_editorial_response(first: dict[str, object]) -> None:
    request_path = Path(str(first["editorial_request_path"]))
    response_path = Path(str(first["editorial_response_path"]))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    source_ref = request["facts"][0]["source_ref"]
    response = {
        "schema_version": "periodic_report_editorial_response_v0",
        "request_digest": request["request_digest"],
        "language": "zh-CN",
        "title": "项目阶段分析周报",
        "kicker": "阶段分析周报",
        "period_label": request["actual_work_window"]["period_label"],
        "highlights": [
            {
                "highlight_id": "coverage",
                "value": "全景",
                "label": "问题覆盖",
                "tone": "positive",
            },
            {
                "highlight_id": "depth",
                "value": "因果",
                "label": "重点下钻",
                "tone": "neutral",
            },
        ],
        "sections": [
            {
                "section_id": "overview",
                "title": "全景判断",
                "items": [
                    {
                        "title": "阶段分析已经形成完整判断",
                        "summary": "当前证据已经覆盖全景分类、重点因果与后续处置。",
                        "source_ref": source_ref,
                    }
                ],
            },
            {
                "section_id": "problem_map",
                "title": "问题版图",
                "items": [
                    {
                        "title": "问题已按影响与证据分层",
                        "summary": "主问题、次要问题与待确认边界已经拆开呈现。",
                        "source_ref": source_ref,
                    }
                ],
            },
            {
                "section_id": "causal_analysis",
                "title": "重点因果下钻",
                "items": [
                    {
                        "title": "最高价值问题已下钻到最窄责任层",
                        "summary": "结论由完成回执支撑，并保留尚未证明的边界。",
                        "source_ref": source_ref,
                        "details": [
                            {"label": "证据", "text": "完成事实已持久化。"},
                            {"label": "边界", "text": "不外推到未验证范围。"},
                        ],
                    },
                    {
                        "title": "跨项共因已经与单点问题分离",
                        "summary": "处置优先级不再由 Todo 时间顺序决定。",
                        "source_ref": source_ref,
                        "details": [
                            {"label": "共因", "text": "证据链可跨条目复核。"},
                            {"label": "单点", "text": "局部现象单独保留。"},
                        ],
                    },
                ],
            },
            {
                "section_id": "coverage_and_actions",
                "title": "版本覆盖与处置",
                "items": [
                    {
                        "title": "历史证据与当前覆盖已经分开",
                        "summary": "当前版本只声明已有证据能证明的覆盖范围。",
                        "source_ref": source_ref,
                    }
                ],
            },
            {
                "section_id": "next_actions",
                "title": "下一步",
                "items": [
                    {
                        "title": "按影响与可证伪性推进下一阶段",
                        "summary": "优先验证能覆盖多个问题族的修复。",
                        "source_ref": source_ref,
                    }
                ],
            },
        ],
    }
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")


def test_pending_intent_projects_a_ts_validated_governed_action(tmp_path: Path) -> None:
    registry, runtime = _fixture(tmp_path)

    dispatch = dispatch_interaction_projection_hooks(
        [
            periodic_report_pending_intent_interaction_hook(
                registry_path=registry,
                runtime_root=runtime,
                goal_id=GOAL_ID,
                agent_id=AGENT_ID,
            )
        ]
    )

    projection = dispatch["projections"]["pending_capability_intent"]
    assert projection["state"] == "pending"
    assert projection["generation_authorized"] is True
    assert projection["external_delivery_authorized"] is True
    assert projection["agent_read_required"] is True
    assert "consume-pending" in projection["command"]

    quiet = {
        "decision": "skip",
        "should_run": False,
        "state": "terminal_no_followup",
        "effective_action": "terminal_no_followup",
        "interaction_contract": {
            "mode": "terminal_no_followup",
            "agent_channel": {"must_attempt": False, "quiet_noop_allowed": True},
        },
    }
    _apply_pending_capability_intent_precedence(quiet, projection)
    assert quiet["effective_action"] == "governed_capability_intent"
    assert quiet["should_run"] is True
    assert quiet["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert quiet["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False
    assert quiet["interaction_contract"]["cli_channel"]["next_cli_actions"] == [
        projection["command"]
    ]

    gated = {
        "decision": "skip",
        "should_run": False,
        "state": "operator_gate",
        "requires_user_action": True,
        "action_required": True,
        "open_count": 1,
        "interaction_contract": {
            "mode": "user_gate",
            "user_channel": {"action_required": True, "notify": "NOTIFY"},
            "agent_channel": {"must_attempt": False, "quiet_noop_allowed": True},
        },
    }
    _apply_pending_capability_intent_precedence(gated, projection)
    assert gated["requires_user_action"] is True
    assert gated["open_count"] == 1
    assert gated["interaction_contract"]["user_channel"] == {
        "action_required": True,
        "notify": "NOTIFY",
    }
    assert gated["interaction_contract"]["agent_channel"]["must_attempt"] is True


def test_disabling_subscription_revokes_pending_automatic_delivery(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["goals"][0]["control_plane"]["periodic_report"] = {
        "enabled": False,
        "timezone": "Asia/Shanghai",
    }
    registry.write_text(json.dumps(payload), encoding="utf-8")

    dispatch = dispatch_interaction_projection_hooks(
        [
            periodic_report_pending_intent_interaction_hook(
                registry_path=registry,
                runtime_root=runtime,
                goal_id=GOAL_ID,
                agent_id=AGENT_ID,
            )
        ]
    )
    consumed = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )

    assert "pending_capability_intent" not in dispatch["projections"]
    assert consumed["status"] == "subscription_disabled"
    assert "deliver_periodic_report_goal_channel" not in (
        registry.parent / "ACTIVE_GOAL_STATE.md"
    ).read_text(encoding="utf-8")


def test_consumption_queues_authorized_delivery_and_exact_replay_does_not_duplicate(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)

    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    assert required["status"] == "editorial_required"
    assert Path(required["editorial_request_path"]).is_file()
    assert not Path(required["editorial_response_path"]).exists()
    state_before = (registry.parent / "ACTIVE_GOAL_STATE.md").read_text(
        encoding="utf-8"
    )
    assert "approve_periodic_report_payload" not in state_before
    _write_editorial_response(required)
    first = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    replay = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )

    assert first["status"] == "delivery_ready"
    assert first["external_delivery_authorized"] is True
    assert first["delivery_authority"] == {
        "schema_version": "periodic_report_delivery_authority_v0",
        "kind": "enabled_periodic_report_subscription",
        "goal_id": GOAL_ID,
        "source": "goal_override",
        "effective_revision": first["delivery_authority"]["effective_revision"],
        "route_ref": "report-route",
    }
    assert first["external_writes_performed"] is False
    assert first["content_checks"] == {
        "schema_version": "periodic_report_content_checks_v0",
        "document_normalized": True,
        "artifact_digests_verified": True,
        "html_self_contained": True,
        "matching_document_digest": True,
        "language_is_zh_cn": True,
        "analysis_narrative_validated": True,
        "evidence_lineage_validated": True,
        "external_writes_performed": False,
    }
    assert Path(first["artifacts"]["html_path"]).is_file()
    assert Path(first["artifacts"]["markdown_path"]).is_file()
    assert Path(first["artifacts"]["generation_bundle_path"]).is_file()
    assert Path(first["artifacts"]["publication_candidate_path"]).is_file()
    html = Path(first["artifacts"]["html_path"]).read_text(encoding="utf-8")
    assert "本期结论：阶段分析已经形成完整判断" in html
    assert "当前风险：问题已按影响与证据分层" in html
    assert "下一步：按影响与可证伪性推进下一阶段" in html
    assert replay["status"] == "no_pending_intent"
    assert (
        pending_periodic_report_intents(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
        )
        == []
    )
    state = (registry.parent / "ACTIVE_GOAL_STATE.md").read_text(encoding="utf-8")
    assert "approve_periodic_report_payload" not in state
    parsed = parse_active_state_todos(state)
    delivery = next(
        item
        for item in parsed["agent_todos"]["items"]
        if item.get("todo_id") == first["delivery_todo_id"]
    )
    assert delivery["status"] == "open"
    assert delivery["action_kind"] == "deliver_periodic_report_goal_channel"
    assert delivery["capability_binding_ref"].startswith("periodic-report:g")
    assert delivery.get("required_decision_scopes", []) == []
    status = collect_status(
        registry_path=registry,
        runtime_root_override=str(runtime),
        scan_roots=[registry.parent],
        limit=20,
        goal_id=GOAL_ID,
    )
    quota = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=["network", "lark_bot_message_write"],
    )
    assert quota["selected_todo"]["todo_id"] == delivery["todo_id"], quota
    assert quota["user_todo_summary"]["open_count"] == 0


def test_agent_typed_request_is_replay_safe_and_settlement_only_retry_deduplicates(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    sidecars = runtime / "goals" / GOAL_ID / "post_writeback_hooks"
    for path in sidecars.iterdir():
        path.unlink()
    source_ref = "om_discussion_is_semantically_selected_by_agent"
    observed_at = "2026-08-30T09:00:00Z"
    source_identity = {
        "provider": "fixture",
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "source_ref": source_ref,
        "observed_at": observed_at,
        "requester_kind": "user",
        "addressing_source": "provider_mention",
        "binding_revision": "sha256:" + "b" * 64,
    }
    source_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                source_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    bind_calls: list[str] = []

    def bind_source(**kwargs: object) -> dict[str, object]:
        bind_calls.append(str(kwargs["source_ref"]))
        return {
            "schema_version": SOURCE_BINDING_RECEIPT_SCHEMA,
            **source_identity,
            "source_digest": source_digest,
            "raw_content_returned": False,
            "external_writes_performed": False,
        }

    settlement_calls: list[bool] = []
    other_settlement_calls: list[bool] = []

    def settle_source(**kwargs: object) -> dict[str, object]:
        receipts = list(
            (runtime / "goals" / GOAL_ID / "periodic_reports").glob("*/receipt.json")
        )
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text(encoding="utf-8"))["status"] == (
            "delivery_ready"
        )
        settlement_calls.append(bool(kwargs["execute"]))
        settled = len(settlement_calls) > 1
        return {
            "ok": settled,
            "schema_version": SOURCE_SETTLEMENT_RECEIPT_SCHEMA,
            "status": "settled" if settled else "failed",
            "write_performed": False,
            "raw_content_returned": False,
            "external_writes_performed": False,
        }

    def settle_other(**kwargs: object) -> dict[str, object]:
        other_settlement_calls.append(bool(kwargs["execute"]))
        raise AssertionError("non-owner adapter must not receive settlement")

    owner_adapter = PeriodicReportRequestAdapter(
        adapter_id="fixture-periodic-report-source",
        bind_source=bind_source,
        settle_source=settle_source,
    )
    other_adapter = PeriodicReportRequestAdapter(
        adapter_id="different-periodic-report-source",
        bind_source=lambda **_kwargs: {},
        settle_source=settle_other,
    )
    ports_forward = PeriodicReportRequestPorts(
        adapters={
            owner_adapter.adapter_id: owner_adapter,
            other_adapter.adapter_id: other_adapter,
        }
    )
    ports_reverse = PeriodicReportRequestPorts(
        adapters={
            other_adapter.adapter_id: other_adapter,
            owner_adapter.adapter_id: owner_adapter,
        }
    )

    accepted = record_periodic_report_request(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        source_ref=source_ref,
        request_ports=ports_forward,
        source_adapter_id=owner_adapter.adapter_id,
        execute=True,
    )
    replay = record_periodic_report_request(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        source_ref=source_ref,
        request_ports=ports_reverse,
        source_adapter_id=None,
        execute=True,
    )

    assert accepted["status"] == "accepted"
    assert replay["status"] == "already_requested"
    assert bind_calls == [source_ref]
    intents = periodic_report_request_intents(
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert len(intents) == 1
    assert source_ref not in json.dumps(intents[0], ensure_ascii=False)
    mismatch = settle_periodic_report_request(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        intent=intents[0],
        request_ports=PeriodicReportRequestPorts(
            adapters={other_adapter.adapter_id: other_adapter}
        ),
        execute=True,
    )
    assert mismatch["status"] == "adapter_unavailable"
    assert other_settlement_calls == []
    assert (
        len(
            periodic_report_request_intents(
                runtime_root=runtime,
                goal_id=GOAL_ID,
                agent_id=AGENT_ID,
            )
        )
        == 1
    )

    editorial_required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    assert editorial_required["status"] == "editorial_required"
    _write_editorial_response(editorial_required)

    first = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
        provider_request_ports=ports_reverse,
    )
    retry = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
        provider_request_ports=ports_forward,
    )

    assert first["status"] == "delivery_ready"
    assert first["source_settlement"]["status"] == "failed"
    assert retry["settlement_only_retry"] is True
    assert retry["source_settlement"]["status"] == "settled"
    assert settlement_calls == [True, True]
    assert other_settlement_calls == []
    durable = next(
        (runtime / "goals" / GOAL_ID / "periodic_reports").glob("*/receipt.json")
    )
    persisted = json.loads(durable.read_text(encoding="utf-8"))
    assert persisted["source_settlement"]["status"] == "settled"
    assert persisted["settlement_only_retry"] is True
    state = (registry.parent / "ACTIVE_GOAL_STATE.md").read_text(encoding="utf-8")
    assert state.count("action_kind=deliver_periodic_report_goal_channel") == 1
    assert (
        periodic_report_request_intents(
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
        )
        == []
    )


def test_typed_request_namespaces_equal_source_refs_by_adapter_under_concurrency(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    source_ref = "provider-local-message-id"
    bind_calls: list[str] = []
    settlement_calls: list[str] = []

    def build_adapter(adapter_id: str) -> PeriodicReportRequestAdapter:
        source_identity = {
            "provider": adapter_id,
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "source_ref": source_ref,
            "observed_at": "2026-08-30T09:00:00Z",
            "requester_kind": "user",
            "addressing_source": "provider_mention",
            "binding_revision": "sha256:" + adapter_id[-1] * 64,
        }
        source_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                source_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        def bind_source(**_kwargs: object) -> dict[str, object]:
            bind_calls.append(adapter_id)
            return {
                "schema_version": SOURCE_BINDING_RECEIPT_SCHEMA,
                **source_identity,
                "source_digest": source_digest,
                "raw_content_returned": False,
                "external_writes_performed": False,
            }

        def settle_source(**kwargs: object) -> dict[str, object]:
            source_receipt = kwargs["source_receipt"]
            assert isinstance(source_receipt, dict)
            assert source_receipt["provider"] == adapter_id
            settlement_calls.append(adapter_id)
            return {
                "ok": True,
                "schema_version": SOURCE_SETTLEMENT_RECEIPT_SCHEMA,
                "status": "settled",
                "write_performed": False,
                "raw_content_returned": False,
                "external_writes_performed": False,
            }

        return PeriodicReportRequestAdapter(
            adapter_id=adapter_id,
            bind_source=bind_source,
            settle_source=settle_source,
        )

    adapter_a = build_adapter("provider-adapter-a")
    adapter_b = build_adapter("provider-adapter-b")
    ports_forward = PeriodicReportRequestPorts(
        adapters={adapter_a.adapter_id: adapter_a, adapter_b.adapter_id: adapter_b}
    )
    ports_reverse = PeriodicReportRequestPorts(
        adapters={adapter_b.adapter_id: adapter_b, adapter_a.adapter_id: adapter_a}
    )

    def record(
        adapter: PeriodicReportRequestAdapter,
        ports: PeriodicReportRequestPorts,
    ) -> dict[str, object]:
        return record_periodic_report_request(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            source_ref=source_ref,
            request_ports=ports,
            source_adapter_id=adapter.adapter_id,
            execute=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(record, adapter_a, ports_forward)
        future_b = executor.submit(record, adapter_b, ports_reverse)
        accepted_a = future_a.result()
        accepted_b = future_b.result()

    assert accepted_a["status"] == accepted_b["status"] == "accepted"
    assert accepted_a["request_id"] != accepted_b["request_id"]
    assert sorted(bind_calls) == [adapter_a.adapter_id, adapter_b.adapter_id]
    assert record(adapter_a, ports_reverse)["status"] == "already_requested"
    assert (
        record_periodic_report_request(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            source_ref=source_ref,
            request_ports=None,
            source_adapter_id=adapter_b.adapter_id,
            execute=True,
        )["status"]
        == "already_requested"
    )
    with pytest.raises(ValueError, match="source adapter is ambiguous"):
        record_periodic_report_request(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            source_ref=source_ref,
            request_ports=ports_forward,
            source_adapter_id=None,
            execute=True,
        )

    intents = periodic_report_request_intents(
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert {intent["source_receipt_id"] for intent in intents} == {
        accepted_a["request_id"],
        accepted_b["request_id"],
    }
    for intent in reversed(intents):
        settlement = settle_periodic_report_request(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            intent=intent,
            request_ports=ports_reverse,
            execute=True,
        )
        assert settlement["status"] == "settled"
    assert sorted(settlement_calls) == [adapter_a.adapter_id, adapter_b.adapter_id]

    journal_path = (
        runtime
        / "goals"
        / GOAL_ID
        / "periodic_report_requests"
        / f"{accepted_a['request_id']}.json"
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["adapter_id"] = adapter_b.adapter_id
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(ValueError, match="journal identity drifted"):
        record(adapter_a, ports_forward)
    journal["adapter_id"] = adapter_a.adapter_id
    journal["source_receipt"]["source_ref"] = "different-source"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(ValueError, match="journal identity drifted"):
        record(adapter_a, ports_forward)


def test_terminal_source_settlement_is_durable_and_not_retried(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    source_ref = "om_terminal_source"
    source_identity = {
        "provider": "fixture",
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "source_ref": source_ref,
        "observed_at": "2026-08-30T09:00:00Z",
        "requester_kind": "user",
        "addressing_source": "provider_mention",
        "binding_revision": "sha256:" + "d" * 64,
    }
    source_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            source_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    settlement_calls: list[bool] = []

    def bind_source(**_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": SOURCE_BINDING_RECEIPT_SCHEMA,
            **source_identity,
            "source_digest": source_digest,
            "raw_content_returned": False,
            "external_writes_performed": False,
        }

    def settle_source(**kwargs: object) -> dict[str, object]:
        settlement_calls.append(bool(kwargs["execute"]))
        return {
            "ok": False,
            "schema_version": SOURCE_SETTLEMENT_RECEIPT_SCHEMA,
            "status": SOURCE_SETTLEMENT_TERMINAL_STATUS,
            "failure_code": "source_receipt_drift",
            "write_performed": False,
            "raw_content_returned": False,
            "external_writes_performed": False,
        }

    adapter = PeriodicReportRequestAdapter(
        adapter_id="fixture-periodic-report-source",
        bind_source=bind_source,
        settle_source=settle_source,
    )
    ports = PeriodicReportRequestPorts(adapters={adapter.adapter_id: adapter})
    accepted = record_periodic_report_request(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        source_ref=source_ref,
        request_ports=ports,
        source_adapter_id=None,
        execute=True,
    )
    intents = periodic_report_request_intents(
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    settlement = settle_periodic_report_request(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        intent=intents[0],
        request_ports=ports,
        execute=True,
    )

    assert settlement["status"] == SOURCE_SETTLEMENT_TERMINAL_STATUS
    assert settlement["failure_code"] == "source_receipt_drift"
    assert settlement["write_performed"] is True
    request_path = (
        runtime
        / "goals"
        / GOAL_ID
        / "periodic_report_requests"
        / f"{accepted['request_id']}.json"
    )
    journal = json.loads(request_path.read_text(encoding="utf-8"))
    assert journal["status"] == "settlement_failed"
    assert journal["settlement"]["failure_code"] == "source_receipt_drift"
    assert periodic_report_request_intents(
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    ) == []
    assert settlement_calls == [True]


def test_report_artifacts_leave_no_temp_residue(tmp_path: Path) -> None:
    registry, runtime = _fixture(tmp_path)

    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    _write_editorial_response(required)
    first = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )

    assert first["status"] == "delivery_ready"
    artifact_dir = Path(first["artifacts"]["markdown_path"]).parent
    names = sorted(item.name for item in artifact_dir.iterdir())
    for expected in (
        "generation-bundle.json",
        "publication-candidate.json",
        "report.html",
        "report.md",
    ):
        assert expected in names
    assert not [name for name in names if name.endswith(".tmp")]
    html = Path(first["artifacts"]["html_path"]).read_text(encoding="utf-8")
    assert html.rstrip().endswith("</html>")
    assert Path(first["artifacts"]["markdown_path"]).read_text(encoding="utf-8").strip()


def test_atomic_write_text_keeps_target_intact_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "report.md"
    _atomic_write_text(target, "previous content")
    assert target.read_text(encoding="utf-8") == "previous content"

    def _boom(source: object, destination: object) -> None:
        raise OSError("simulated interruption before rename")

    monkeypatch.setattr(
        "loopx.capabilities.periodic_report.pending_intent.os.replace", _boom
    )
    with pytest.raises(OSError, match="simulated interruption"):
        _atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "previous content"
    assert not [item for item in tmp_path.iterdir() if item.name.endswith(".tmp")]


def test_report_markdown_not_left_behind_when_rename_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, runtime = _fixture(tmp_path)
    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    _write_editorial_response(required)

    real_replace = os.replace

    def _interrupt_at_report_md(source: object, destination: object) -> None:
        if Path(str(destination)).name == "report.md":
            raise OSError("simulated interruption before report.md rename")
        real_replace(source, destination)

    monkeypatch.setattr(
        "loopx.capabilities.periodic_report.pending_intent.os.replace",
        _interrupt_at_report_md,
    )
    with pytest.raises(OSError, match="before report.md rename"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )

    assert list(runtime.rglob("report.md")) == []


def test_report_html_not_left_behind_when_rename_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, runtime = _fixture(tmp_path)
    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    _write_editorial_response(required)

    real_replace = os.replace

    def _interrupt_at_report_html(source: object, destination: object) -> None:
        if Path(str(destination)).name == "report.html":
            raise OSError("simulated interruption before report.html rename")
        real_replace(source, destination)

    monkeypatch.setattr(
        "loopx.capabilities.periodic_report.pending_intent.os.replace",
        _interrupt_at_report_html,
    )
    with pytest.raises(OSError, match="before report.html rename"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )

    assert list(runtime.rglob("report.html")) == []
    assert not [item for item in runtime.rglob("*") if item.name.endswith(".tmp")]


def test_atomic_write_temp_file_stays_in_directory_and_never_collides_with_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "report.html"

    observed: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def _tracking_replace(source: object, destination: object) -> None:
        observed.append((Path(str(source)), Path(str(destination))))
        real_replace(source, destination)

    monkeypatch.setattr(
        "loopx.capabilities.periodic_report.pending_intent.os.replace",
        _tracking_replace,
    )
    _atomic_write_text(target, "tracked content")

    assert len(observed) == 1
    source, destination = observed[0]
    assert source.parent == destination.parent == tmp_path
    assert source.name.startswith(".report.html.")
    assert source.name.endswith(".tmp")
    assert source != destination
    assert sorted(item.name for item in tmp_path.iterdir()) == ["report.html"]
    assert target.read_text(encoding="utf-8") == "tracked content"


def test_atomic_write_text_consecutive_rewrites_replace_content_without_residue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report.md"
    drafts = ("first draft", "second draft", "final draft")
    for content in drafts:
        _atomic_write_text(target, content)
        assert target.read_text(encoding="utf-8") == content
        assert sorted(item.name for item in tmp_path.iterdir()) == ["report.md"]

    _atomic_write_text(target, drafts[-1])
    assert target.read_text(encoding="utf-8") == drafts[-1]
    assert sorted(item.name for item in tmp_path.iterdir()) == ["report.md"]


def test_atomic_write_text_large_multi_segment_content_keeps_head_and_tail(
    tmp_path: Path,
) -> None:
    segment = "数据段落" * 40 + "\n"
    content = "<!-- head-marker -->\n" + segment * 2000 + "<!-- tail-marker -->\n"
    target = tmp_path / "report.md"

    _atomic_write_text(target, content)

    written = target.read_text(encoding="utf-8")
    assert written == content
    assert len(written) == len(content)
    assert written.startswith("<!-- head-marker -->")
    assert written.rstrip().endswith("<!-- tail-marker -->")


def test_atomic_write_text_fsync_failure_cleans_temp_and_keeps_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "report.md"
    _atomic_write_text(target, "previous content")
    assert target.read_text(encoding="utf-8") == "previous content"

    def _boom(fd: object) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(
        "loopx.capabilities.periodic_report.pending_intent.os.fsync", _boom
    )
    with pytest.raises(OSError, match="simulated fsync failure"):
        _atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "previous content"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["report.md"]


def test_consumption_uses_the_stage_progress_snapshot(tmp_path: Path) -> None:
    registry, runtime = _fixture(tmp_path)
    sidecar = next(
        (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json")
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["intent"]["payload"]["project_progress"] = build_project_progress_snapshot(
        registry_path=registry,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        completed_at="2026-08-30T09:00:00Z",
    )
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Follow-up work added after stage completion",
        claimed_by=AGENT_ID,
        agent_id=AGENT_ID,
    )

    result = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    assert result["status"] == "editorial_required"
    request = json.loads(
        Path(result["editorial_request_path"]).read_text(encoding="utf-8")
    )
    facts = request["facts"]

    assert any("Finish the bounded analysis" in fact["title"] for fact in facts)
    completed_fact = next(
        fact for fact in facts if "Finish the bounded analysis" in fact["title"]
    )
    assert completed_fact["completed_at"] == "2026-08-30T09:00:00Z"
    assert request["actual_work_window"]["period_label"] == (
        "2026-08-30 17:00（北京时间）"
    )
    assert not any(
        "Follow-up work added after stage completion" in fact["title"] for fact in facts
    )


def test_consumption_skips_done_todos_without_valid_completion_timestamps(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    state_path = registry.parent / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        state_path.read_text(encoding="utf-8")
        + "- [x] Handwritten outcome without a timestamp.\n"
        "  <!-- loopx:todo todo_id=todo_handwritten status=done"
        f" task_class=advancement_task claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )

    result = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    assert result["status"] == "editorial_required"
    request = json.loads(
        Path(result["editorial_request_path"]).read_text(encoding="utf-8")
    )
    facts = request["facts"]

    assert [fact["source_ref"] for fact in facts] == ["todo:todo_finished"]
    assert facts[0]["completed_at"] == "2026-08-30T09:00:00Z"
    assert not any("Handwritten outcome" in fact["title"] for fact in facts)


def test_publication_candidate_keeps_the_trigger_snapshot_baseline(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    first_candidate = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_first",
        trigger_receipt={"coalesced_trigger_ids": ["trigger_first"]},
        facts=[
            {
                "source_ref": "todo:first",
                "title": "First outcome",
                "summary": "The first outcome was published.",
                "content_kind": "outcome",
                "status": "done",
            }
        ],
        baseline=None,
    )
    cursor_one = commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=first_candidate,
        publication_id="goal-channel:first",
        delivered_at="2026-08-30T08:00:00Z",
        covered_until="2026-08-30T08:00:00Z",
    )
    baseline_one = periodic_report_incremental_baseline(cursor_one)
    sidecar = next(
        (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json")
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["intent"]["payload"]["project_progress"] = {
        "schema_version": "periodic_report_project_progress_projection_v0",
        "goal_id": GOAL_ID,
        "observed_at": "2026-08-30T09:00:00Z",
        "language": "zh-CN",
        "items": [
            {
                "item_id": "second",
                "title": "Second outcome",
                "summary": "The second outcome belongs to this stage.",
                "content_kind": "outcome",
                "status": "done",
                "source_ref": "todo:second",
                "completed_at": "2026-08-30T09:00:00Z",
                "change_kind": "added",
            }
        ],
        "incremental_baseline": baseline_one,
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    competing_candidate = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_competing",
        trigger_receipt={"coalesced_trigger_ids": ["trigger_competing"]},
        facts=[
            {
                "source_ref": "todo:competing",
                "title": "Competing outcome",
                "summary": "Another report reached publication first.",
                "content_kind": "outcome",
                "status": "done",
            }
        ],
        baseline=baseline_one,
    )
    cursor_two = commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=competing_candidate,
        publication_id="goal-channel:competing",
        delivered_at="2026-08-30T09:01:00Z",
        covered_until="2026-08-30T09:00:30Z",
    )
    _write_editorial_response(required)

    result = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    frozen_candidate = json.loads(
        Path(result["artifacts"]["publication_candidate_path"]).read_text(
            encoding="utf-8"
        )
    )

    assert frozen_candidate["incremental_baseline"] == baseline_one
    assert (
        frozen_candidate["incremental_baseline"]["cursor_id"] != cursor_two["cursor_id"]
    )
    with pytest.raises(ValueError, match="baseline does not match"):
        commit_periodic_report_publication_cursor(
            runtime_root=runtime,
            candidate=frozen_candidate,
            publication_id="goal-channel:second",
            delivered_at="2026-08-30T09:02:00Z",
            covered_until="2026-08-30T09:00:00Z",
        )


def test_consumption_rejects_snapshot_outcome_after_stage_completion(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    sidecar = next(
        (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json")
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["intent"]["payload"]["project_progress"] = {
        "schema_version": "periodic_report_project_progress_projection_v0",
        "goal_id": GOAL_ID,
        "observed_at": "2026-08-30T09:00:00Z",
        "language": "zh-CN",
        "items": [
            {
                "item_id": "completed_1",
                "title": "Future outcome",
                "summary": "This timestamp is outside the frozen stage.",
                "content_kind": "outcome",
                "value_rank": 10,
                "source_ref": "todo:future",
                "completed_at": "2026-08-30T09:00:01Z",
            }
        ],
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot timestamp is invalid"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )


def test_consumption_recovers_when_delivery_todo_precedes_receipt_write(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)

    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    _write_editorial_response(required)
    first = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    receipt_path = Path(first["artifacts"]["html_path"]).parent / "receipt.json"
    receipt_path.unlink()

    recovered = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )

    assert recovered["status"] == "delivery_ready"
    assert recovered["generation_receipt"] == first["generation_receipt"]
    state = (registry.parent / "ACTIVE_GOAL_STATE.md").read_text(encoding="utf-8")
    assert state.count("deliver_periodic_report_goal_channel") == 1
    assert "approve_periodic_report_payload" not in state


def test_consumption_rejects_english_or_flat_editorial_response(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    _write_editorial_response(required)
    response_path = Path(required["editorial_response_path"])
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["title"] = "Weekly project report"
    response_path.write_text(json.dumps(response), encoding="utf-8")

    with pytest.raises(ValueError, match="title must be Chinese-first"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )

    _write_editorial_response(required)
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["period_label"] = "2026-08-23 — 2026-08-30"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="must match the actual work window"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )

    _write_editorial_response(required)
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["title"] = "项目阶段分析周报"
    response["sections"] = response["sections"][::-1]
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="overview-to-depth contract"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )

    _write_editorial_response(required)
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["sections"][0]["items"][0]["item_id"] = "authored_id"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="item_id is assigned by the consumer"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )

    _write_editorial_response(required)
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["raw_content"] = "must not enter a frozen report"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden raw/private field"):
        consume_pending_periodic_report_intent(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            execute=True,
        )


def test_consumption_reuses_the_exact_frozen_fact_request(tmp_path: Path) -> None:
    registry, runtime = _fixture(tmp_path)
    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    request_path = Path(required["editorial_request_path"])
    original = request_path.read_text(encoding="utf-8")
    state_path = registry.parent / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "validated-analysis-outcome", "later-mutable-registry-prose"
        ),
        encoding="utf-8",
    )

    replay = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )

    assert replay["status"] == "editorial_required"
    assert request_path.read_text(encoding="utf-8") == original


def test_consumption_derives_the_period_from_actual_report_facts(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    state_path = registry.parent / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "updated_at=2026-08-30T09:00:00Z",
            "updated_at=2026-08-29T22:42:26+08:00",
        ),
        encoding="utf-8",
    )

    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    request = json.loads(
        Path(required["editorial_request_path"]).read_text(encoding="utf-8")
    )

    assert request["actual_work_window"] == {
        "start_at": "2026-08-29T14:42:26+00:00",
        "end_at": "2026-08-30T09:00:00+00:00",
        "period_label": "2026-08-29 22:42 — 2026-08-30 17:00（北京时间）",
        "source": "agent_run_history_or_report_facts",
    }


def test_consumption_prefers_real_agent_run_start_over_completion_timestamps(
    tmp_path: Path,
) -> None:
    registry, runtime = _fixture(tmp_path)
    run_dir = runtime / "goals" / GOAL_ID / "runs"
    run_dir.mkdir(parents=True)
    (run_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-29T19:24:27+08:00",
                "agent_id": AGENT_ID,
                "classification": "state_refreshed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    required = consume_pending_periodic_report_intent(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
    )
    request = json.loads(
        Path(required["editorial_request_path"]).read_text(encoding="utf-8")
    )

    assert request["actual_work_window"]["start_at"] == ("2026-08-29T11:24:27+00:00")
    assert request["actual_work_window"]["period_label"] == (
        "2026-08-29 19:24 — 2026-08-30 17:00（北京时间）"
    )


def test_cross_agent_or_malformed_intent_fails_closed(tmp_path: Path) -> None:
    registry, runtime = _fixture(tmp_path)
    assert (
        pending_periodic_report_intents(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id="other-agent",
        )
        == []
    )
    sidecar = next(
        (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json")
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["intent"]["requested_write_scope"] = ["external_delivery"]
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        pending_periodic_report_intents(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
        )
        == []
    )


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_real_cli_editorial_fallback_reads_canonical_work_without_display(
    tmp_path, monkeypatch, provider
):
    """The public command freezes canonical facts and sends nothing externally."""
    import subprocess
    import sys
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
    )
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime = _fixture(tmp_path)
    state = registry.parent / "ACTIVE_GOAL_STATE.md"
    records = parse_active_state_todos(state.read_text(), item_limit=None)[
        "agent_todos"
    ]["items"]
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=records, handoff_mode="soft_claim"
    )
    initialize_canonical_authority(
        runtime, GOAL_ID, projection, state_path=state, provider=provider
    )
    # A runtime override must route both intent IO and the Todo snapshot.
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = str(tmp_path / "unused-runtime")
    registry.write_text(json.dumps(config))
    state.unlink()
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "periodic-report",
        "consume-pending",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--execute",
    ]
    run = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "editorial_required", result
    frozen = Path(result["editorial_request_path"])
    before = frozen.read_bytes()
    request = json.loads(before)
    assert {fact["source_ref"] for fact in request["facts"]} == {"todo:todo_finished"}
    assert not state.exists()
    retry = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert retry.returncode == 0, retry.stderr
    assert frozen.read_bytes() == before, (
        "retry must retain the same authored-input basis"
    )
