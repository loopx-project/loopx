from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.runtime.agent_evidence_history import goal_history_runs
from loopx.control_plane.work_items.replan_context_codec import project_replan_context, replan_history_from_status


def _run(**changes):
    return {
        "goal_id": "evidence-goal", "agent_id": "agent-a",
        "generated_at": "2026-08-18T01:00:00Z",
        "classification": "bounded_probe",
        "health_check": "Negative result changes the next probe.",
        **changes,
    }


def test_history_contract_errors_are_not_empty_results():
    assert goal_history_runs({"goals": []}, "evidence-goal") == []
    assert goal_history_runs({"goals": [{"id": "evidence-goal", "latest_runs": []}]}, "evidence-goal") == []
    for bad in [{}, {"ok": False, "goals": []}, {"goals": None},
                {"goals": [{"id": "evidence-goal"}]}, {"runs": [None]}]:
        with pytest.raises(ValueError):
            goal_history_runs(bad, "evidence-goal")
    for status in [{}, {"run_history": None}, {"run_history": {"goals": [{"id": "evidence-goal"}]}}]:
        with pytest.raises(ValueError):
            replan_history_from_status(status, "evidence-goal")


def test_projection_rejects_invalid_typed_records_and_redacts_prose():
    for invalid in [None, {}, {"schema_version": "future"}]:
        with pytest.raises(ValueError):
            project_replan_context(goal_id="evidence-goal", agent_id="agent-a",
                                   runs=[_run(progress_observation=invalid)])
    context = project_replan_context(
        goal_id="evidence-goal", agent_id="agent-a",
        runs=[_run(health_check="access_key=private-value"),
              _run(agent_id="agent-b", health_check="other Agent detail")],
    )
    assert len(context["evidence"]) == 1
    assert "private-value" not in json.dumps(context)
    assert "other Agent detail" not in json.dumps(context)


def test_real_history_reads_the_projected_reference_and_rejects_stale_scope(tmp_path: Path):
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(runtime),
        "goals": [{"id": "evidence-goal", "status": "active-read-only", "domain": "fixture"}],
    }))
    index = runtime / "goals/evidence-goal/runs/index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps(_run()) + "\n")
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
                                    source_status={"registry": str(registry), "runtime_root": str(runtime),
                                                   "run_history": {"goals": []}})
    evidence = context["evidence"][0]
    prefix = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json"]
    args = shlex.split(evidence["read_action"])[1:]
    assert "--registry" in args and "--runtime-root" in args
    result = subprocess.run([*prefix, *args], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["evidence"]["health_check"] == _run()["health_check"]
    for agent in ("agent-b", "missing-agent"):
        rejected = subprocess.run([*prefix, *[agent if arg == "agent-a" else arg for arg in args]],
                                  capture_output=True, text=True, check=False)
        assert rejected.returncode == 1
        assert "unavailable" in json.loads(rejected.stdout)["error"]
    index.write_text("")
    stale = subprocess.run([*prefix, *args], capture_output=True, text=True, check=False)
    assert stale.returncode == 1
    assert "unavailable" in json.loads(stale.stdout)["error"]
    removed = subprocess.run([*prefix, "evidence-log"], capture_output=True, text=True, check=False)
    assert removed.returncode == 2
    assert "invalid choice" in removed.stderr
    assert not (runtime / "goals/evidence-goal/rollout-event-log.jsonl").exists()


def test_old_evidence_survives_display_limit_and_core_goal_uses_registered_state(tmp_path: Path):
    runtime, project = tmp_path / "runtime", tmp_path / "project"
    project.mkdir()
    (project / "ACTIVE_GOAL_STATE.md").write_text("# Goal\n\n## Objective\n> Deliver the accepted outcome across the whole workload.\n\n## Agent Todo\n- Run another probe.\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "evidence-goal", "repo": str(project), "state_file": "ACTIVE_GOAL_STATE.md",
        "objective": "Original registered objective", "status": "active-read-only", "domain": "fixture",
    }]}))
    index = runtime / "goals/evidence-goal/runs/index.jsonl"
    index.parent.mkdir(parents=True)
    old = _run(progress_observation={"schema_version": "typed_progress_observation_v0",
        "result_class": "advanced", "work_item_id": "old-work", "evidence_ids": ["old-best"]})
    newer = [_run(generated_at=f"2026-08-19T01:{i//60:02}:{i%60:02}Z", progress_observation={
        "schema_version": "typed_progress_observation_v0", "result_class": "unchanged",
        "work_item_id": "new-work", "evidence_ids": ["repeat-proof"]}) for i in range(180)]
    index.write_text("".join(json.dumps(row) + "\n" for row in [old, *newer]))
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
        source_status={"registry": str(registry), "runtime_root": str(runtime), "run_history": {
            "goals": [{"id": "evidence-goal", "latest_runs": newer[-3:]}]}},
        goal_acceptance_contract={"enabled": True, "scope": {"kind": "selected_work", "todo_ids": ["new-work"]},
                                  "objective": "Validate only the new route"})
    assert context["from_full_index"] is True
    assert context["evidence_count"] == 181
    assert len(context["evidence"]) == 2
    assert context["core_goal"]["objective"] == "Deliver the accepted outcome across the whole workload."
    assert context["core_goal"]["acceptance_contract"]["scope"]["kind"] == "selected_work"
    command = context["evidence"][-1]["read_action"]
    result = subprocess.run([sys.executable, "-m", "loopx.cli", *shlex.split(command)[1:]], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["evidence"]["progress_observation"]["evidence_ids"] == ["old-best"]


def test_truncated_display_cannot_make_an_old_blocker_new():
    from loopx.control_plane.work_items.progress_observation import semantic_delta_from_writeback

    rows = [_run(generated_at=f"2026-08-19T01:{i:02}:00Z", progress_observation={
        "schema_version": "typed_progress_observation_v0", "result_class": "blocked",
        "blocker_id": f"blocker-{i}", "evidence_ids": [f"proof-{i}"],
    }) for i in range(40)]
    obligation = {"obligation_id": "replan-a", "triggers": [{"kind": "typed_progress_repeat"}],
                  "progress_baseline": rows[-1]["progress_observation"]}
    obligation["replan_context"] = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows, obligation=obligation)
    assert obligation["replan_context"]["coverage_truncated"] is True
    assert not any(row.get("blocker_id") == "blocker-0" for row in obligation["replan_context"]["coverage_ledger"])
    with pytest.raises(ValueError, match="complete evidence history"):
        semantic_delta_from_writeback(obligation=obligation, progress_observation=rows[0]["progress_observation"])
    result = semantic_delta_from_writeback(obligation=obligation, progress_observation=rows[0]["progress_observation"], history_runs=rows)
    assert result["accepted"] is False
    assert result["reason_code"] == "progress_observation_replayed"


def test_dense_context_uses_shared_private_snapshot_transport(monkeypatch):
    from loopx.control_plane.work_items import replan_history_codec

    expected = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=[_run()])
    monkeypatch.setattr(replan_history_codec, "MAX_REQUEST_BYTES", 1)
    assert project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=[_run()]) == expected


def test_unscoped_replan_assignment_ignores_evidence_and_presentation_changes():
    from loopx.control_plane.goals.goal_frontier import autonomous_replan_scope_decision

    obligation = {"schema_version": "autonomous_replan_obligation_v0", "required": True,
                  "triggers": [{"kind": "periodic_review_due"}]}
    selected = []
    for extra in [{}, {"recommended_action": "different presentation"},
                  {"replan_context": {"evidence": [{"summary": "new observation"}]}},
                  {"replan_novelty_policy": {"evidence_source": "legacy-source"}}]:
        decisions = [
            autonomous_replan_scope_decision({**obligation, **extra}, agent_id=agent,
                                            registered_agent_ids=["agent-a", "agent-b"])
            for agent in ["agent-a", "agent-b"]
        ]
        assert sum(decision["applies"] for decision in decisions) == 1
        selected.extend(decision["selected_peer_agent"] for decision in decisions)
    assert len(set(selected)) == 1


def test_native_recovery_reads_its_event_source_without_the_removed_cli(tmp_path: Path):
    from loopx.kunluncode_goal_mode.control_plane import LoopXControlPlane

    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": []}))
    log = runtime / "goals/evidence-goal/rollout-event-log.jsonl"
    log.parent.mkdir(parents=True)
    event = {"schema_version": "loopx_rollout_event_v0", "goal_id": "evidence-goal",
             "agent_id": "agent-a", "todo_id": "todo-a", "event_id": "event-a",
             "event_kind": "refresh_state", "classification": "kunluncode_native_goal_verified",
             "recorded_at": "2026-08-18T01:00:00Z"}
    log.write_text("\n".join(json.dumps(item) for item in [
        event, {**event, "agent_id": "agent-b"}, {**event, "todo_id": "todo-b"},
        {**event, "recorded_at": "2026-08-17T01:00:00Z"},
    ]) + "\n")
    before = log.read_bytes()
    control = LoopXControlPlane(tmp_path, {"registry": "registry.json",
                                         "goal_id": "evidence-goal", "agent_id": "agent-a"})
    result = control.evidence_since("2026-08-18T00:00:00Z", todo_id="todo-a")
    assert result["ledger_count"] == 1
    assert result["ledger"][0]["event_id"] == "event-a"
    assert log.read_bytes() == before
