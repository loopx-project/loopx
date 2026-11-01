"""Real write/read/replan paths at the bounded vision authoring limits."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from loopx.control_plane.testing.canary_harness import import_fixture_authority


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("complete_prior", [False, True])
def test_projected_autonomous_example_updates_evidence_without_changing_goal(
    tmp_path, monkeypatch, provider, complete_prior,
):
    from canonical_authority_fixture import isolate_sqlite_runtime

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("replan_authoring_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, project = fixture.write_fixture(tmp_path)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    original_goal = "## Goal\n\nPreserve the original objective and verify each scoped result.\n\n"
    state.write_text(state.read_text().replace("## Next Action", original_goal + "## Next Action"))
    import_fixture_authority(registry, fixture.GOAL_ID, provider=provider)
    document = tmp_path / "vision.json"
    prior_patch = {"acceptance_summary": "Original evidence pending."}
    if complete_prior:
        prior_patch.update({
            "vision_summary": "Verify the whole original delivery with evidence.",
            "role_scope": "Own this bounded verification; do not change peer work.",
            "advancement_policy": "repeat_until_closed",
            "replan_trigger_summary": "Keep the remaining evidence frontier visible.",
            "dreaming_policy": "Propose only evidence-backed alternatives.",
            "last_patch_summary": "Retain the existing role and continuation intent.",
        })
    fixture.write_json(document, {"vision_patch": prior_patch})
    fixture.run_cli(registry, runtime, vision_path=document, check=True,
                    dry_run=False, autonomous_replan_recorded=False)

    guard = fixture.run_quota(registry, runtime)
    assert guard["effective_action"] == "autonomous_replan_required"
    assert guard["requires_user_action"] is False
    contract = guard["replan_action_packet"]["writeback_contract"]["vision_authoring"]
    packet = copy.deepcopy(contract["minimal_example"])
    assert "JSON replaces the complete packet" in contract["authoring_hint"]
    assert "full vision_patch" in contract["authoring_hint"]
    if complete_prior:
        # Follow the returned authoring procedure using the complete current
        # Agent packet from a separate read, not fields reconstructed by a model.
        current = fixture.run_status(registry, runtime)
        goal = next(g for g in current["run_history"]["goals"] if g["id"] == fixture.GOAL_ID)
        current_packet = copy.deepcopy(goal["latest_runs"][0]["agent_vision"])
        assert current_packet["agent_id"] == fixture.AGENT_ID
        current_packet["vision_patch"].update(packet["vision_patch"])
        current_packet["path_delta"] = packet["path_delta"]
        packet = current_packet
    fixture.write_json(document, packet)
    result = fixture.run_cli(registry, runtime, vision_path=document,
        check=False, dry_run=False, autonomous_replan_recorded=True)
    accepted = fixture.payload(result)
    assert result.returncode == 0, accepted.get("error") or accepted
    assert accepted["ok"] and accepted["appended"]
    assert accepted["agent_vision"]["path_delta"]["outcome"] == "replan"
    assert original_goal in state.read_text()

    # A separate CLI read must preserve every existing field, not merely echo
    # an accepted summary while discarding direction, role or repeat policy.
    expected_patch = prior_patch | packet["vision_patch"]
    cold = fixture.run_status(registry, runtime)
    goal = next(g for g in cold["run_history"]["goals"] if g["id"] == fixture.GOAL_ID)
    assert goal["latest_runs"][0]["agent_vision"]["vision_patch"] == expected_patch
    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"
    assert json.loads(index.read_text().splitlines()[-1])["agent_vision"]["vision_patch"] == expected_patch
    if complete_prior:
        assert goal["latest_runs"][0]["agent_vision"]["state"] == current_packet["state"]

    # Updating evidence is still a durable change under the existing rule.
    # A caller replacing the projected disposition must fail before writing.
    before = index.read_bytes()
    packet["vision_patch"]["acceptance_summary"] = "Another evidence update."
    packet["path_delta"]["outcome"] = "continue"
    fixture.write_json(document, packet)
    rejected = fixture.run_cli(registry, runtime, vision_path=document,
        check=False, dry_run=False, autonomous_replan_recorded=True)
    assert rejected.returncode == 1
    assert "changes durable fields acceptance_summary" in fixture.payload(rejected)["error"]
    assert index.read_bytes() == before

    # A review that retains the durable fields may continue without changing
    # the Goal, asking its owner, or inventing a direction change.
    packet["vision_patch"] = accepted["agent_vision"]["vision_patch"]
    for disposition in ("continue", "no_change"):
        packet["path_delta"]["outcome"] = disposition
        fixture.write_json(document, packet)
        retained = fixture.payload(fixture.run_cli(registry, runtime, vision_path=document,
            check=True, dry_run=False, autonomous_replan_recorded=True))
        assert retained["agent_vision"]["vision_patch"] == packet["vision_patch"]
        assert retained["agent_vision"]["path_delta"]["outcome"] == disposition
        assert original_goal in state.read_text()


@pytest.mark.parametrize("character", ["x", "界"])
def test_full_budget_roundtrips_without_erasing_replan_or_partial_writes(tmp_path, character):
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("vision_budget_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, project = fixture.write_fixture(tmp_path / "current")
    import_fixture_authority(registry, fixture.GOAL_ID, provider="file")
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
    import_fixture_authority(baseline_registry, fixture.GOAL_ID, provider="file")
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
    assert "usage: vision_patch=1120, path_delta=681, stride_decision=0, fallback_declarations=0" in fixture.payload(rejected)["error"]
    assert index.read_bytes() == before_index
    assert state.read_bytes() == before_state


def test_misplaced_delta_rejects_before_write_and_corrected_packet_roundtrips(tmp_path):
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("vision_packet_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, project = fixture.write_fixture(tmp_path)
    import_fixture_authority(registry, fixture.GOAL_ID, provider="file")
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
    import_fixture_authority(registry, fixture.GOAL_ID, provider="file")
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
        assert persisted["path_delta"]["outcome"] == "replan"
        assert persisted["path_delta"]["evidence_refs"]


def test_historical_stride_probe_whitespace_roundtrips_with_strategy_off(tmp_path):
    source = Path(__file__).resolve().parents[2] / "examples/project/goal-vision-refresh-state-budget-smoke.py"
    spec = importlib.util.spec_from_file_location("historical_stride_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    registry, runtime, _ = fixture.write_fixture(tmp_path)
    import_fixture_authority(registry, fixture.GOAL_ID, provider="file")
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
