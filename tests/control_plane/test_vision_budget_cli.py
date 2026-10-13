"""Real write/read/replan paths at the bounded vision authoring limits."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("character", ["x", "界"])
def test_full_budget_roundtrips_without_erasing_replan_or_partial_writes(tmp_path, character):
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("vision_budget_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, project = fixture.write_fixture(tmp_path / "current")
    packet = {
        "state": "vision_closed",
        "vision_patch": {
            "vision_summary": character * 420,
            "acceptance_summary": character * 420,
            "role_scope": character * 280,
        },
        # 420 + 420 + 280 + 320 + 320 + 6 + 34 = 1800, independent of the validator.
        "path_delta": {"outcome": "replan", "prior_assumption": character * 320,
                       "observed_reality": character * 320, "changed": [character * 34]},
    }
    path = tmp_path / "vision.json"
    baseline = copy.deepcopy(packet)
    baseline["vision_patch"]["vision_summary"] = character * 220
    baseline["vision_patch"]["acceptance_summary"] = character * 220
    baseline["path_delta"]["prior_assumption"] = character * 220
    baseline["path_delta"]["observed_reality"] = character * 220
    baseline_registry, baseline_runtime, _ = fixture.write_fixture(tmp_path / "control")
    fixture.write_json(path, baseline)
    fixture.run_cli(baseline_registry, baseline_runtime, vision_path=path, check=True,
                    dry_run=False, autonomous_replan_recorded=False)
    baseline_quota = fixture.run_quota(baseline_registry, baseline_runtime)
    fixture.write_json(path, packet)
    result = fixture.payload(fixture.run_cli(registry, runtime, vision_path=path,
        check=True, dry_run=False, autonomous_replan_recorded=False))
    assert result["ok"] is True
    assert result["agent_vision"]["vision_budget"]["total_usage"] == 1800
    assert result["agent_vision"]["vision_budget"]["total_limit"] == 1800
    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    before_index, before_state = index.read_bytes(), state.read_bytes()
    persisted = json.loads(index.read_text().splitlines()[-1])["agent_vision"]
    assert persisted["path_delta"]["observed_reality"] == character * 320

    # Read-model compaction must not truncate newly valid authoring back to 220.
    status = fixture.run_status(registry, runtime)
    goal = next(g for g in status["run_history"]["goals"] if g["id"] == fixture.GOAL_ID)
    assert goal["latest_runs"][0]["agent_vision"]["path_delta"] == persisted["path_delta"]
    quota = fixture.run_quota(registry, runtime)
    assert quota["effective_action"] == "autonomous_replan_required"
    assert quota["interaction_contract"]["agent_channel"]["must_attempt"] is True
    # This replan lane is not the CLI budget suite's "small" fixture. Check
    # growth against the same lane with an old-limit packet, not that fixture's cap.
    output_growth = len(json.dumps(quota, ensure_ascii=False)) - len(json.dumps(baseline_quota, ensure_ascii=False))
    assert output_growth <= 600

    packet["path_delta"]["changed"] = [character * 35]
    fixture.write_json(path, packet)
    rejected = fixture.run_cli(registry, runtime, vision_path=path, check=False,
                              dry_run=False, autonomous_replan_recorded=False)
    assert rejected.returncode == 1
    assert "total_agent_vision uses 1801 chars; limit is 1800" in fixture.payload(rejected)["error"]
    assert index.read_bytes() == before_index
    assert state.read_bytes() == before_state


def test_misplaced_delta_rejects_before_write_and_corrected_packet_roundtrips(tmp_path):
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("vision_packet_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, project = fixture.write_fixture(tmp_path)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    before = state.read_bytes()
    delta = {"schema_version": "goal_path_delta_v0", "outcome": "replan",
             "prior_assumption": "Keep the route.", "observed_reality": "A dependency changed.",
             "changed": ["Use the successor."]}
    packet = {"vision_patch": {"vision_summary": "Deliver the successor."},
              "goal_path_delta_v0": delta}
    path = tmp_path / "vision.json"
    fixture.write_json(path, packet)
    rejected = fixture.run_cli(registry, runtime, vision_path=path, check=False,
                               dry_run=False, autonomous_replan_recorded=False)
    assert rejected.returncode == 1
    assert "must be supplied as agent_vision.path_delta" in fixture.payload(rejected)["error"]
    assert state.read_bytes() == before
    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"
    assert not index.exists()
    packet["path_delta"] = packet.pop("goal_path_delta_v0")
    packet["telemetry"] = {"outcome": "ok", "evidence_refs": ["evidence:probe"]}
    fixture.write_json(path, packet)
    result = fixture.payload(fixture.run_cli(registry, runtime, vision_path=path, check=True,
        dry_run=False, autonomous_replan_recorded=False))
    assert result["agent_vision"]["path_delta"] == delta
    assert json.loads(index.read_text().splitlines()[-1])["agent_vision"]["path_delta"] == delta


@pytest.mark.parametrize("omitted", [None, "path_delta", "evidence_refs"])
def test_authoring_example_material_closeout_has_no_missing_path_replan(tmp_path, omitted):
    from loopx.control_plane.work_items.progress_observation import replan_writeback_requirements

    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("vision_closeout_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, _ = fixture.write_fixture(tmp_path)
    contract = replan_writeback_requirements({
        "satisfying_semantic_outcomes": ["fresh_vision_path_outcome"],
    })["writeback_contract"]["vision_authoring"]
    # Consumer test: execute the projected example, but derive the expected
    # acceptance independently: missing a path or evidence must still replan.
    packet = copy.deepcopy(contract["minimal_example"])
    if omitted == "path_delta":
        packet.pop("path_delta", None)
    elif omitted == "evidence_refs":
        packet.get("path_delta", {}).pop("evidence_refs", None)
    path = tmp_path / "vision.json"
    fixture.write_json(path, packet)
    result = fixture.payload(fixture.run_cli(
        registry, runtime, vision_path=path, check=True,
        dry_run=False, autonomous_replan_recorded=False,
    ))
    assert result["vision_checkpoint"]["satisfied"] is True
    quota = fixture.run_quota(registry, runtime)
    gaps = quota["goal_frontier_projection"]["acceptance_gaps"]
    outcome_gaps = [g for g in gaps if g["kind"] == "vision_outcome_checkpoint_required"]
    assert bool(outcome_gaps) is (omitted is not None)
    if omitted:
        checks = outcome_gaps[0]["component_checks"]
        assert checks["evidence_refs_present"] is False
        assert checks["path_outcome_valid"] is (omitted == "evidence_refs")
    else:
        persisted = result["agent_vision"]
        assert persisted["path_delta"]["outcome"] == "continue"
        assert persisted["path_delta"]["evidence_refs"]


def test_historical_stride_probe_whitespace_roundtrips_with_strategy_off(tmp_path):
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("historical_stride_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, _ = fixture.write_fixture(tmp_path)
    expected = {"scope": "One complete experiment.", "prediction": "Distinguish the remaining explanations.",
                "adoption_criterion": "Retain a validated gain.", "cost_bound": "One bounded comparison."}
    packet = {"vision_patch": {"vision_summary": "Preserve the historical proposal."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The recorded route is bounded.",
                       "observed_reality": "The historical read contract remains applicable.",
                       "retained": ["The recorded proposal"], "evidence_refs": ["evidence:historical-probe"]},
        "stride_decision": {
        "schema_version": "stride_decision_v1", "strategy_version": "evidence_conditioned_v1",
        "reference_context_id": "replan-context-" + "a" * 24,
        "reference_obligation_id": "replan-" + "a" * 16, "basis": "unexplained_gap",
        "next_probe": {key: " " * 4000 + value + " " * 4000 for key, value in expected.items()}}}
    path = tmp_path / "vision.json"
    fixture.write_json(path, packet)
    completed = fixture.run_cli(registry, runtime, vision_path=path,
        check=False, dry_run=False, autonomous_replan_recorded=False)
    result = fixture.payload(completed)
    assert completed.returncode == 0, result
    assert result["agent_vision"]["stride_decision"]["next_probe"] == expected
    usage = result["agent_vision"]["vision_budget"]["field_usage"]
    for key, value in expected.items():
        assert usage["stride_decision.next_probe." + key] == len(value)
    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"
    persisted = json.loads(index.read_text().splitlines()[-1])["agent_vision"]
    assert persisted["stride_decision"]["next_probe"] == expected
    status = fixture.run_status(registry, runtime)
    goal = next(g for g in status["run_history"]["goals"] if g["id"] == fixture.GOAL_ID)
    assert goal["latest_runs"][0]["agent_vision"]["stride_decision"]["next_probe"] == expected
    read_budget = goal["latest_runs"][0]["agent_vision"]["vision_budget"]
    for key in ("status", "field_usage", "total_usage", "total_limit"):
        assert read_budget[key] == persisted["vision_budget"][key]

    # Malformed supplied historical values must reject before any append, not
    # be silently replaced by the compatibility mapping. Omission above is legal.
    vector = {"work_slice": "shrink", "route_distance": "grow", "validation_scope": "shrink"}
    changes = [{"axes": value} for value in (None, False, {**vector, "extra": "hold"})]
    for field in ("reference_context_id", "reference_obligation_id"):
        for value in ([packet["stride_decision"][field]], {"value": packet["stride_decision"][field]}):
            changes.append({field: value})
    journal_before = index.read_bytes()
    for change in changes:
        invalid = copy.deepcopy(packet)
        invalid["stride_decision"].update(change)
        fixture.write_json(path, invalid)
        rejected = fixture.run_cli(registry, runtime, vision_path=path,
            check=False, dry_run=False, autonomous_replan_recorded=False)
        assert rejected.returncode != 0, change
        assert index.read_bytes() == journal_before
    after = fixture.run_status(registry, runtime)
    after_goal = next(g for g in after["run_history"]["goals"] if g["id"] == fixture.GOAL_ID)
    assert after_goal["latest_runs"][0]["agent_vision"] == goal["latest_runs"][0]["agent_vision"]
