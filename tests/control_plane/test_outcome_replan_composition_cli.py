"""Outcome review and due replan use the same ordered facts in preview/execute.

The public CLI uses disposable canonical providers; no model or experiment is
needed to prove stale-context refusal, recovery and once-only settlement.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _bind_selected_replan_guard,
    _initialize_fixture_authority, _projected_cli_args, _run_cli,
    _spend_run_count, _write_fixture,
)


def _review_packet(control: dict | None) -> dict:
    packet = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {
            "acceptance_summary": "Preserve the export content and validate its recovery.",
            "advancement_policy": "repeat_until_closed",
        },
        "path_delta": {
            "outcome": "replan", "prior_assumption": "The export needs a complete validation slice.",
            "observed_reality": "The current artifact still needs independent recovery evidence.",
            "retained": ["Original content requirement and independent validation"],
            "changed": ["Validate the artifact and its recovery together"],
            "evidence_refs": ["artifact:export-validation"],
        },
    }
    if control is not None:
        packet["stride_decision"] = {
            "schema_version": "stride_decision_v2", "strategy_version": "evidence_conditioned_v1",
            "reference_context_id": control["reference_context_id"],
            "reference_obligation_id": control["reference_obligation_id"],
            "basis": "uncertain_evidence", "action": "verify",
            "axes": {
                "work_slice": {"plan": "Validate the artifact and its recovery together.",
                               "reason": "One complete slice answers the remaining qualification gap."},
                "route_distance": {"plan": "Retain the current export implementation.",
                                   "reason": "No result yet supports replacing it."},
                "validation_scope": {"plan": "Check valid, invalid and replayed exports.",
                                     "reason": "Recovery evidence must retain the original content boundary."},
            },
            "next_probe": {
                "scope": "Complete one export recovery validation.",
                "minimum_evidence": "Valid output, refused invalid input and once-only replay.",
                "prediction": "The current implementation preserves content across recovery.",
                "adoption_criterion": "Retain only independently validated recovery behavior.",
                "cost_bound": "One artifact and its related failure/replay cases.",
            },
        }
    return packet


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("stride", [False, True])
def test_batched_review_previews_post_refinement_basis_and_recovers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, stride: bool,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    registered = json.loads(registry.read_text())
    registered["goals"][0].update(status="active")
    registered["goals"][0]["quota"]["allowed_slots"] = 4
    registry.write_text(json.dumps(registered))
    _initialize_fixture_authority(project, runtime, provider=provider)

    def call(*args: str, expected_code: int = 0) -> dict:
        code, result = _run_cli(registry, runtime, *args, cwd=project)
        assert code == expected_code, result
        return result

    def inspect() -> dict:
        return call("goal-acceptance", "inspect", "--goal-id", GOAL_ID)

    call("configure-goal", "--goal-id", GOAL_ID, "--execution-replan-after-turns", "1",
         *(["--execution-stride-strategy", "evidence_conditioned_v1"] if stride else []), "--execute")
    original = {"outcome_id": "export", "owner_agent_id": AGENT_ID,
        "objective": "Deliver a recoverable export", "todo_ids": [TODO_ID],
        "requirements": [{"id": "content", "description": "Preserve the original content"}],
        "source": {"ref": "message:export-request", "summary": "The owner requires preserved content"}}
    declaration = tmp_path / "declaration.json"
    declaration.write_text(json.dumps(original))
    call("goal-acceptance", "refine", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--document", str(declaration), "--operation-id", "declare-export",
         "--expected-provider-revision", inspect()["provider_revision"], "--execute")
    prior = "export-artifact-turn"
    call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
         "--agent-id", AGENT_ID, "--turn-instance-id", prior)
    written = call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", prior, "--classification", "artifact_validated",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks")
    call(*_projected_cli_args(written["settlement_owed"]["command"], turn_instance_id=prior))

    due = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
               "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID)
    assert "periodic_review_due" in {row["kind"] for row in due["autonomous_replan_obligation"]["triggers"]}
    bound = _bind_selected_replan_guard(registry, runtime, project, TURN_ID, todo_id=TODO_ID)
    control = bound["replan_action_packet"].get("stride_control") if stride else None
    packet = _review_packet(control)
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(packet))
    command = bound["interaction_contract"]["cli_channel"]["next_cli_actions"][0].replace(
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(decision))
    refresh = (*_projected_cli_args(command, turn_instance_id=TURN_ID), "--no-global-sync", "--suppress-external-sinks")
    before = inspect()
    update = tmp_path / "update.json"
    review = {"id": "content", "conclusion": "uncertain", "evidence_refs": ["artifact:export-validation"],
              "note": "The artifact retains content; independent recovery validation remains open"}
    update.write_text(json.dumps({"operation_id": "export-milestone",
        "expected_provider_revision": before["provider_revision"],
        "refinement": {"outcome_id": "export", "review": [review]}}))
    batch = (*refresh, "--goal-outcome-update-json", str(update))
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    original_index = index.read_bytes()
    preview = call(*batch, "--dry-run", expected_code=1 if stride else 0)
    assert preview["goal_outcome_update"]["status"] == "planned"
    if stride:
        assert preview["replan_transition"]["reason_code"] == "stride_review_basis_mismatch"
    assert inspect() == before and index.read_bytes() == original_index
    assert _spend_run_count(runtime) == 1

    # Execution keeps its sequential, non-atomic contract. A rejected review
    # must retain the independently committed refinement receipt.
    actual = call(*batch, expected_code=1 if stride else 0)
    assert actual["goal_outcome_update"]["status"] == "applied"
    after = inspect()
    assert after["goal_acceptance_contract"]["outcome_requirements"]["revision"] == 2
    if stride:
        assert actual["appended"] is False
        assert actual["replan_transition"]["reason_code"] == "stride_review_basis_mismatch"
        assert index.read_bytes() == original_index
        fresh = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID)
        assert fresh["heartbeat_receipt"]["settlement_identity"] == bound["heartbeat_receipt"]["settlement_identity"]
        current = fresh["replan_action_packet"]["stride_control"]
        assert current["reference_context_id"] != control["reference_context_id"]
        # Read the current facts before authoring against this new basis. Merely
        # replaying the stale packet still cannot settle the review.
        context = fresh["interaction_contract"]["agent_channel"]["work_context"]
        acceptance = next(row["content"] for row in context["sources"] if row.get("source") == "goal_acceptance")
        assert acceptance["goal_acceptance_contract"] == after["goal_acceptance_contract"]
        replay = call(*batch, expected_code=1)
        assert replay["goal_outcome_update"]["status"] == "replayed"
        assert inspect() == after and index.read_bytes() == original_index
        decision.write_text(json.dumps(_review_packet(current)))
        actual = call(*refresh)
        assert "goal_outcome_update" not in actual
    assert actual["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    spent = call(*_projected_cli_args(actual["settlement_owed"]["command"], turn_instance_id=TURN_ID))
    assert spent["ok"] and _spend_run_count(runtime) == 2
    call(*_projected_cli_args(actual["settlement_owed"]["command"], turn_instance_id=TURN_ID))
    assert _spend_run_count(runtime) == 2 and inspect() == after
    retained = after["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert retained["sources"] == [original["source"]]
    assert retained["requirements"][0]["description"] == original["requirements"][0]["description"]
    assert retained["requirements"][0]["agent_review"]["conclusion"] == "uncertain"
