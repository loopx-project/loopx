"""Captured replan reads separate decision facts from authoring without re-admission."""
from __future__ import annotations

import copy
import json
import shlex
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, restart_effect_runtime
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, build_turn_envelope, quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from test_replan_host_context_projection import _quota_payload
from test_replan_progress_native_cli import (
    AGENT, GOAL, TODO, TURN, call_progress_cli, make_progress_cli_fixture,
)


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def without_example(contract):
    expected = copy.deepcopy(contract)
    if "vision_authoring" in expected:
        expected["vision_authoring"].pop("minimal_example")
    return expected


@pytest.mark.parametrize("stride", ["off", "evidence_conditioned_v1"])
def test_staged_packet_budget_and_lossless_authority(stride):
    source = _quota_payload(stride_strategy=stride)
    legacy = build_turn_envelope(source)
    source["replan_read_path"] = "/synthetic/decision.json"
    original = copy.deepcopy(source)
    envelope = build_turn_envelope(source, captured_decision_path=source["replan_read_path"])
    packet = envelope["replan_action_packet"]
    assert "--replan-stage review" in packet["review"]
    assert "--replan-stage writeback" in packet["writeback"]
    assert "planning_guidance" not in packet and "stride_control" not in packet
    assert json_bytes(packet) <= 2048
    assert json_bytes(packet) < json_bytes(legacy["replan_action_packet"]) / 2
    assert envelope["writeback"] == legacy["writeback"]
    for field in ("boundary", "effective_action", "user", "scheduler"):
        assert envelope.get(field) == legacy.get(field)
    assert turn_envelope_action_signature_document(envelope) == quota_action_signature_document(source)
    params = {"payload": source, "source_hash": envelope["action_signature"]["source_decision_hash"]}
    review = _turn_envelope_result("replan_view", **params, stage="review")
    writer = _turn_envelope_result("replan_view", **params, stage="writeback")
    context = source["autonomous_replan_obligation"]["replan_context"]
    assert review["core_goal"] == context["core_goal"]
    assert review["planning_horizon"] == source.get("planning_horizon")
    assert review["uncovered_frontier"] == context["uncovered_frontier"]
    assert review["coverage_count"] == context["coverage_count"]
    assert review["coverage_truncated"] == context["coverage_truncated"]
    assert [row["evidence_ref"] for row in review["evidence"]] == [row["evidence_ref"] for row in context["evidence"]]
    assert writer["writeback_contract"] == without_example(source["replan_action_packet"]["writeback_contract"])
    assert writer["allowed_terminal"] == source["replan_action_packet"]["allowed_terminal"]
    assert review["context_id"] == writer["context_id"] == context["context_id"]
    assert json_bytes(review) <= 16384
    assert "guidance" not in review
    assert json_bytes(writer) <= 16384
    if stride != "off":
        control = writer["stride_control"]
        assert control["previous_decision_ref"] == source["replan_action_packet"]["stride_control"]["previous_decision_ref"]
        assert "decision_followthrough" not in control and "decision_value_review" not in control
        assert "minimal_example" not in control
        assert control["basis_options"] == ["supported_progress", "unexplained_gap", "uncertain_evidence", "useful_new_route", "decision_failure"]
        assert {"basis", "action", "axes", "next_probe"} <= set(control["required_fields"])
    assert source == original


def test_review_retains_scoped_dependencies_and_unknown_outcomes():
    source = _quota_payload()
    source["replan_read_path"] = "/synthetic/decision.json"
    # These are canonical typed planning facts, never text classified as blockers.
    source["planning_horizon"] = {"items": [
        {"todo_id": "todo-report", "state": "waiting"},
        {"todo_id": "todo-implementation", "state": "runnable"},
    ], "relations": [{"from_todo_id": "todo-report", "to_todo_id": "todo-measurement", "kind": "depends_on"}]}
    context = source["autonomous_replan_obligation"]["replan_context"]
    context["decision_followthrough"] = {"recorded_progress_count": 21,
        "result_counts": {"advanced": 1, "blocked": 20, "unchanged": 0, "no_followup": 0, "exploration_exhausted": 0},
        "observations": [{"todo_id": "todo-report", "result_class": "blocked", "delivery_outcome": None}],
        "execution_and_goal_gain": "unknown"}
    envelope = build_turn_envelope(source)
    review = _turn_envelope_result("replan_view", payload=source, stage="review",
        source_hash=envelope["action_signature"]["source_decision_hash"])
    assert review["planning_horizon"] == source["planning_horizon"]
    assert review["decision_followthrough"]["observation_count"] == 21
    assert review["decision_followthrough"]["result_counts"]["advanced"] == 1
    assert review["decision_followthrough"]["execution_and_goal_gain"] == "unknown"
    assert "recommended_action" not in review  # No hardcoded pivot or automatic wait.


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("envelope", [False, True])
def test_real_guard_capture_read_and_writeback_without_another_guard(tmp_path, monkeypatch, provider, envelope):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    capture = tmp_path / "capture"
    try:
        initial = call_progress_cli(fixture, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN)
        assert initial["decision"] == "autonomous_replan_required"
        # Exercise the emitted command verbatim even with a hostile ambient
        # PATH entry. The captured reader belongs to the originating checkout.
        pinned_cli = str(Path(sys.executable).with_name("loopx"))
        guard = call_progress_cli(fixture, "--cli-bin", pinned_cli, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--todo-id", TODO, "--decision-output-dir", str(capture),
            *(["--turn-envelope"] if envelope else []))
        packet = guard["replan_action_packet"]
        saved = (capture / "decision.json").read_bytes()
        source = json.loads(saved)
        original_files = {key: fixture[key].read_bytes() for key in ("registry", "state", "index")}
        # Executable commands come from the production packet. Deliberately use
        # an absent registry: the reader must never collect state or re-admit.
        trap = tmp_path / "bin"
        trap.mkdir()
        (trap / "loopx").write_text("#!/bin/sh\nexit 73\n")
        (trap / "loopx").chmod(0o700)
        reader_fixture = {**fixture, "registry": tmp_path / "missing-registry.json"}
        def read(stage, expected_code=0):
            args = shlex.split(packet[stage])
            assert args[:3] == [pinned_cli, "--cli-bin", pinned_cli]
            result = subprocess.run([args[0], "--registry", str(reader_fixture["registry"]), *args[1:]],
                cwd=fixture["project"], env={**os.environ, "PATH": str(trap) + os.pathsep + os.environ["PATH"],
                    "LOOPX_USAGE_PING": "0"}, capture_output=True, text=True, timeout=60)
            assert result.returncode == expected_code, (result.stdout, result.stderr)
            return json.loads(result.stdout)
        review, writer = read("review"), read("writeback")
        assert review["context_id"] == writer["context_id"]
        assert review["obligation_id"] == source["replan_action_packet"]["obligation_id"]
        assert writer["writeback_contract"] == without_example(source["replan_action_packet"]["writeback_contract"])
        assert (capture / "decision.json").read_bytes() == saved
        assert {key: fixture[key].read_bytes() for key in original_files} == original_files
        assert not reader_fixture["registry"].exists()
        # Changed bytes cannot masquerade as the admitted observation.
        source["goal_id"] = "another-goal"
        (capture / "decision.json").write_text(json.dumps(source))
        rejected = read("review", expected_code=1)
        assert "source hash" in rejected["reason"]
        (capture / "decision.json").write_bytes(saved)
        # A valid next operation still reaches the existing writer and semantic gate.
        result = call_progress_cli(fixture, *fixture["refresh"])
        assert result["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    finally:
        restart_effect_runtime(wait_for_exit=True)


def _views(source):
    envelope = build_turn_envelope(source)
    params = {"payload": source, "source_hash": envelope["action_signature"]["source_decision_hash"]}
    return envelope, _turn_envelope_result("replan_view", **params, stage="review"), params


def test_populated_history_and_cumulative_acceptance_have_separate_budgets(tmp_path):
    from replan_packet_fixture import populated_replan_source

    base = populated_replan_source(tmp_path / "base")
    envelope, review, _ = _views(base)
    context = base["autonomous_replan_obligation"]["replan_context"]
    assert context["evidence_count"] == 1500 and len(context["evidence"]) == 24
    assert context["from_full_index"] is True
    assert {row["planning_state"] for row in review["planning_horizon"]["work_items"]} == {"selected", "waiting", "runnable"}
    assert context["coverage_truncated"] and context["evidence_truncated"]
    assert review["decision_followthrough"]["observation_count"] == 21
    assert review["decision_followthrough"]["omitted_observation_count"] == 15
    assert sum(review["decision_followthrough"]["result_counts"].values()) == 21
    assert review["decision_followthrough"]["result_counts"]["advanced"] < 21
    no_planning = copy.deepcopy(base)
    no_planning.pop("planning_horizon")
    no_planning.pop("action_portfolio")
    _, history_review, _ = _views(no_planning)
    assert json_bytes(history_review) <= 16 * 1024
    assert 0 < json_bytes(review) - json_bytes(history_review) <= 8 * 1024
    assert json_bytes(envelope["replan_action_packet"]) <= 2 * 1024

    large = populated_replan_source(tmp_path / "acceptance", acceptance=True)
    large_envelope, expanded, params = _views(large)
    # Assert the complete emitted envelope too: moving the payload into a second
    # hot carrier would satisfy a packet-only budget while defeating the fix.
    assert abs(json_bytes(large_envelope) - json_bytes(envelope)) <= 512
    assert "context_read" in large_envelope["action"]["planning_horizon"]
    original = large["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert json_bytes(original) > 200 * 1024
    assert expanded["planning_horizon"]["core_goal_ref"] == "$.core_goal"
    index = expanded["acceptance_index"]
    assert len(index["outcomes"]) == 24
    # Cumulative acceptance earns its own index allocation; it cannot spend the
    # generic review allowance by duplicating entire historical reviews.
    assert 0 < json_bytes(expanded) - json_bytes(review) <= 12 * 1024
    originals = original["acceptance_contract"]["outcome_requirements"]["outcomes"]
    for row, outcome in zip(index["outcomes"], originals, strict=True):
        read = _turn_envelope_result("replan_view", **params, stage="acceptance", outcome_ref=row["outcome_ref"])
        assert read["outcome"] == outcome
        assert row["requirement_count"] == len(outcome["requirements"])
    # A different source revision with the same outcome id must remain visible.
    large["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["outcomes"][0]["revision"] = 3
    _, revised, params = _views(large)
    same_id = [row for row in revised["acceptance_index"]["outcomes"] if row["id"] == "outcome-0"]
    assert len(same_id) == 2 and same_id[0]["outcome_ref"] != same_id[1]["outcome_ref"]
    with pytest.raises(EffectRuntimeRejected, match="exact captured outcome reference"):
        _turn_envelope_result("replan_view", **params, stage="acceptance", outcome_ref="missing")


def test_explore_uses_its_own_incremental_budget_and_real_result_log(tmp_path):
    from replan_packet_fixture import populated_explore_context, populated_replan_source

    explore = populated_explore_context(tmp_path)
    assert explore["ok"] and explore["graph_enabled"]
    base = populated_replan_source(tmp_path / "base")
    enabled = populated_replan_source(tmp_path / "enabled", explore_context=explore)
    base_envelope, base_review, _ = _views(base)
    envelope, review, params = _views(enabled)
    assert "capabilities" not in base_review
    # Optional capability context must not inflate the shared hot packet.
    assert json_bytes(envelope["replan_action_packet"]) - json_bytes(base_envelope["replan_action_packet"]) <= 128
    assert 0 < json_bytes(review) - json_bytes(base_review) <= 768
    detail = _turn_envelope_result("replan_view", **params, stage="explore")
    basis = enabled["autonomous_replan_obligation"]["replan_context"]["explore_basis"]
    assert detail["explore_basis"] == basis
    assert basis["availability"] == "available" and basis["finding_refs"]
    assert basis["result_page"]["total"] > len(basis["finding_refs"])
    assert json_bytes(detail) <= 8 * 1024
    # Execute the capability reader through the shipped CLI as well as its TS owner.
    captured = tmp_path / "explore-decision.json"
    enabled["replan_read_path"] = str(captured)
    captured.write_text(json.dumps(enabled))
    _, cli_review, _ = _views(enabled)
    command = shlex.split(cli_review["capabilities"]["explore"]["read_action"])
    reader_fixture = {"registry": tmp_path / "absent.json", "runtime": tmp_path / "absent-runtime", "project": tmp_path}
    cli_detail = call_progress_cli(reader_fixture, *command[3:])
    assert cli_detail["explore_basis"] == basis
    assert {row["status"] for row in basis["finding_refs"]} >= {"confirmed", "refuted"}
    _, _, params = _views(enabled)
    writer = _turn_envelope_result("replan_view", **params, stage="writeback")
    assert writer["stride_control"]["reference_context_id"] == review["context_id"]
    assert json_bytes(writer) <= 16 * 1024
