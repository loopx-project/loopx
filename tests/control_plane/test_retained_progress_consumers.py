"""Retained progress stays readable without entering accepted delivery sinks."""
from __future__ import annotations

import json

import pytest

from loopx import cli
from loopx.cli_commands import project_lifecycle_refresh_state as refresh_cli
from loopx.capabilities.periodic_report import post_writeback_hook as report_hooks
from loopx.control_plane.capability_hooks import PostWritebackHookRegistration, POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION
from loopx.control_plane.goals.checkpoint_context_io import first_delivery_progress
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload
from tests.control_plane.test_outcome_replan_composition_cli import _review_packet
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, _configure_completion_validation_todo,
    _configure_selected_todo_replan_fixture, _initialize_fixture_authority,
    _projected_cli_args, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("suppress", [False, True])
def test_retained_result_cannot_authorize_delivery_sinks(provider, suppress, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "shared"))
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _configure_completion_validation_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "retained-consumer-original-turn"
    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn)
    rc, guard = _run_cli(registry, runtime, *guard_args, cwd=project)
    if rc:
        [selection] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        rc, guard = _run_cli(registry, runtime, *_projected_cli_args(selection, turn_instance_id=turn), cwd=project)
    assert rc == 0, guard
    first = guard["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
    deliveries = []
    hooks = []
    def producer(_):
        hooks.append(turn)
        return {"schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "consumer.qualified", "capability_id": "periodic-report",
            "phase": "post_writeback", "status": "not_applicable", "intent": None}
    registration = PostWritebackHookRegistration(hook_id="consumer.qualified", capability_id="periodic-report",
        event_kinds=("refresh_state",), intent_kinds=("periodic_report.trigger_evaluation",), requested_read_scope=(), producer=producer)
    monkeypatch.setattr(report_hooks, "periodic_report_post_writeback_hooks_for_goal", lambda **_: (registration,))
    def sink(**kwargs):
        if kwargs["external_sink_delivery_authorized"]:
            deliveries.append(kwargs["goal_id"])
        return {"ok": True, "enabled": False, "skipped": True}
    for name in ("sync_explore_graph_after_material_refresh", "sync_human_gate_after_refresh", "sync_blocked_notice_after_refresh"):
        monkeypatch.setattr(refresh_cli, name, sink)
    monkeypatch.chdir(project)
    def call(*args):
        rc = cli.main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args])
        return rc, read_cli_policy_payload(capsys.readouterr().out, returncode=rc)
    def refresh(command):
        return call(*_projected_cli_args(command, turn_instance_id=turn), "--no-global-sync",
            *(("--suppress-external-sinks",) if suppress else ()))
    def observe(expected):
        index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
        contexts = index.parent.parent / "checkpoint-contexts"
        original = {p: p.read_bytes() for p in [index, *contexts.glob("*.json")]}
        readback = read_heartbeat_settlement(runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
            todo_id=SELECTED_REPLAN_TODO_ID, turn_instance_id=turn, registry_path=registry)
        assert first_delivery_progress(runtime, readback, registry_path=registry)["stage"] == expected
        assert all(p.read_bytes() == content for p, content in original.items())
        return readback
    rc, result = refresh(first)
    assert rc == 0 and result["appended"] is True, result
    readback = observe("direction_pending")
    assert readback.writeback_run["vision_checkpoint"]["satisfied"] is False
    assert readback.spend_qualification.failure is not None
    assert _spend_run_count(runtime) == 0
    # Existing suppression permission is separate from current qualification.
    assert result["external_sink_delivery_authorized"] is (not suppress), result
    assert deliveries == [] and hooks == []
    original = readback.writeback_run
    original_artifact = (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_bytes()
    rc, cold = call(*guard_args, "--context-delivery", "off")
    assert rc == 0, cold
    [_, supplement, spend] = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    rc, context = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0, context
    packet = project / "current-consumer-direction.json"
    packet.write_text(json.dumps(_review_packet(guard["replan_action_packet"]["stride_control"])), encoding="utf-8")
    supplement = supplement.replace("<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(packet))
    supplement = supplement.replace("<read-context-id>", context["read_context_id"])
    rc, qualified = refresh(supplement)
    assert rc == 0 and qualified["vision_checkpoint"]["satisfied"] is True, qualified
    assert qualified["external_sink_delivery_authorized"] is (not suppress)
    assert len(deliveries) == (0 if suppress else 3)
    assert hooks == [turn], json.dumps(qualified.get("post_writeback_hooks"))
    assert observe("settlement_pending").spend_qualification.failure is None
    assert _spend_run_count(runtime) == 0
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    assert index.read_bytes().startswith(original_artifact)
    assert json.loads(original_artifact.splitlines()[-1]) == original
    rc, paid = call(*_projected_cli_args(spend, turn_instance_id=turn))
    assert rc == 0 and paid["settlement_progress"]["state"] == "settled", paid
    observe("settled")
    rc, replay = refresh(supplement)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert hooks == [turn] and _spend_run_count(runtime) == 1
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("settlement_identity") == original["settlement_identity"]
        and row.get("vision_checkpoint") is not None for row in rows) == 2
