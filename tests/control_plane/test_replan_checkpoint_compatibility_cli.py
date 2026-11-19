"""Real persisted acceptance checkpoints survive the legacy producer boundary."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import test_goal_acceptance_cli as acceptance_fixture
from test_goal_acceptance_cli import _configure
from managed_effect_runtime_fixture import stop_disposable_effect_runtimes
from legacy_acceptance_checkpoint_fixture import legacy_acceptance_checkpoint_cli
from loopx.control_plane.coordination.canonical_snapshot import read_canonical_snapshot
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals.goal_frontier.acceptance import (
    acceptance_gaps_from_held_goal_binding, project_legacy_acceptance_hold_ack,
)
from loopx.control_plane.work_items.autonomous_replan_ack import compact_autonomous_replan_ack
from loopx.control_plane.work_items.replan_semantics_codec import project_replan_semantics

acceptance_goal = acceptance_fixture.acceptance_goal
GOAL, AGENT = "goal-acceptance", "agent-a"


@pytest.fixture
def legacy_run(acceptance_goal):
    project, _, _, _ = acceptance_goal
    source = os.environ.get("LOOPX_TEST_CHECKPOINT_BASE_SOURCE")
    yield legacy_acceptance_checkpoint_cli(project, base_source=Path(source).resolve() if source else None)
    stop_disposable_effect_runtimes(project.parent)


def _blocker_writeback(project, document_path, run, obligation, turn):
    artifact = project / "legacy-owner-review.json"
    # An actual controlled process reads the owner document and records the
    # exact missing association; the original receipt owns this one result.
    subprocess.run([sys.executable, "-c",
        "import json,sys; from pathlib import Path; d=json.loads(Path(sys.argv[1]).read_text()); "
        "assert d['bindings']==[]; p=Path(sys.argv[2]); assert not p.exists(); "
        "p.write_text(json.dumps({'todo_id':'todo_export','missing':'owner association',"
        "'objective':d['objective']},sort_keys=True))", str(document_path), str(artifact)], check=True)
    evidence = "owner-review-sha256:" + sha256(artifact.read_bytes()).hexdigest()
    vision = project / "legacy-review-vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "agent_id": AGENT, "state": "active",
        "vision_patch": {"acceptance_summary": json.loads(document_path.read_text())["objective"],
            "advancement_policy": "as_needed"},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "no_change",
            "prior_assumption": "The export needs its original owner association.",
            "observed_reality": "The owner document contains no association for this export.",
            "retained": ["The original contract and held operation."], "evidence_refs": [evidence]},
    }))
    binding = ("--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn,
        "--replan-obligation-id", obligation)
    code, refreshed = run("refresh-state", *binding, "--progress-scope", "agent_lane",
        "--classification", "external_evidence_replan_blocked", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
        "--progress-blocker-id", "owner-association-missing", "--progress-evidence-id", evidence,
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    assert code == 0 and refreshed["settlement_result"]["ok"] is True, refreshed
    ack = refreshed["autonomous_replan_ack"]
    assert ack["recorded"] and ack["semantic_delta"]["accepted"]
    assert ack["semantic_delta"]["obligation_id"] == obligation
    assert ack["semantic_delta"]["satisfying_outcomes"] == ["new_concrete_blocker"]
    spend = ("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    code, spent = run(*spend)
    assert code == 0 and spent["settlement_result"]["ok"] is True, spent
    return spend, ack


@pytest.mark.parametrize("settled_before_upgrade", [True, False], ids=["settled", "admitted"])
def test_legacy_hold_retains_original_ack_and_turn(acceptance_goal, legacy_run, settled_before_upgrade):
    project, document_path, cli, run = acceptance_goal
    document = json.loads(document_path.read_text())
    document["bindings"] = []
    document_path.write_text(json.dumps(document))
    code, noted = legacy_run("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT,
        "--todo-id", "todo_export", "--update-operation-id", "legacy-source-observation",
        "--note", "Record the original export before its owner review.")
    assert code == 0, noted
    def legacy_cli(*args):
        return legacy_run("goal-acceptance", *args, "--goal-id", GOAL)
    contract = _configure(legacy_cli, document_path)["goal_acceptance_contract"]
    turn = "turn-legacy-acceptance-hold"
    guard_args = ("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", turn)
    code, guard = legacy_run(*guard_args)
    assert code == 0 and guard["effective_action"] == "autonomous_replan_required", guard
    obligation = guard["autonomous_replan_obligation"]["obligation_id"]
    if settled_before_upgrade:
        spend, ack = _blocker_writeback(project, document_path, legacy_run, obligation, turn)
        code, quiet = legacy_run("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT)
        assert code == 0 and quiet["should_run"] is False, quiet
        (project.parent / "legacy-ack-readback.json").write_text(json.dumps({"guard": guard, "ack": ack}, indent=2))
    stop_disposable_effect_runtimes(project.parent)
    if settled_before_upgrade:
        code, current = run("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT)
        (project.parent / "upgraded-readback.json").write_text(json.dumps(current, indent=2))
        assert code == 0 and current["should_run"] is False, current
        assert current.get("autonomous_replan_obligation") is None
    else:
        code, current = run(*guard_args)
        (project.parent / "upgraded-readback.json").write_text(json.dumps(current, indent=2))
        assert code == 0, current
        assert current["autonomous_replan_obligation"]["obligation_id"] == obligation
        spend, ack = _blocker_writeback(project, document_path, run, obligation, turn)
    assert cli("inspect")[1]["goal_acceptance_contract"]["digest"] == contract["digest"]
    code, replayed = run(*spend)
    assert code == 0 and replayed["idempotent_replay"] and replayed["appended"] is False, replayed
    for event, edit in enumerate((("--priority", "P3"), ("--note", "Same reviewed hold"),
                                  ("--text", "Reword the same held export"))):
        code, updated = run("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", "todo_export", "--update-operation-id", f"after-upgrade-display-{event}", *edit)
        assert code == 0, updated
        code, current = run("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT)
        assert code == 0 and current["should_run"] is False, current
        assert current.get("autonomous_replan_obligation") is None
    if settled_before_upgrade:
        _assert_exact_source_proof(project)
    document["objective"] = "Produce an export and prove its checksum matches the revised criterion"
    document_path.write_text(json.dumps(document))
    revised = _configure(cli, document_path)["goal_acceptance_contract"]
    assert revised["revision"] == contract["revision"] + 1
    code, current = run("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT)
    assert code == 0 and current["effective_action"] == "autonomous_replan_required", current
    assert current["autonomous_replan_obligation"]["obligation_id"] != obligation


def _assert_exact_source_proof(project):
    """Wrong and missing anchors cannot approve a timestamp-bearing old ACK."""
    runtime = project.parent / "runtime"
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    before = index.read_bytes()
    original_run = next(row for row in (json.loads(line) for line in before.decode().splitlines())
                        if row.get("autonomous_replan_ack", {}).get("recorded") is True)
    ack = compact_autonomous_replan_ack(original_run)
    # Actual status retention compacts this run again; do not lose its source.
    ack = compact_autonomous_replan_ack({"agent_id": AGENT, "generated_at": original_run["generated_at"],
                                       "autonomous_replan_ack": ack})
    assert ack["todo_source"] == original_run["todo_source"]
    current = read_canonical_snapshot(rpc=effect_runtime_result, runtime_root=str(runtime),
        goal_id=GOAL, include_leases=False, projection_readback=None, timeout=30)
    assert current["status"] == "loaded"
    gaps = acceptance_gaps_from_held_goal_binding(
        {"goal_acceptance_contract": current["goal_acceptance_contract"]}, current["todos"], agent_id=AGENT)

    def project(candidate):
        projected = project_legacy_acceptance_hold_ack(candidate, acceptance_gaps=gaps,
            goal_id=GOAL, agent_id=AGENT, runtime_root=str(runtime))
        accepted = project_replan_semantics({"operation": "vision_ack", "ack": projected,
                                            "acceptance_gaps": gaps})["acknowledged"]
        return projected, accepted

    mapped, accepted = project(ack)
    assert accepted and mapped["semantic_delta"]["obligation_id"] == ack["semantic_delta"]["obligation_id"]
    assert mapped["semantic_delta"]["trigger_checkpoints"] != ack["semantic_delta"]["trigger_checkpoints"]
    for changed in ({"store_identity": "file:another-goal-instance"},
                    {"provider_revision": "wrong-provider-revision"}, {"cursor": "999999"},
                    {"source_authority": "unsupported-source"}):
        invalid = {**ack, "todo_source": {**ack["todo_source"], **changed}}
        rejected, accepted = project(invalid)
        assert accepted is False
        assert rejected["semantic_delta"] == ack["semantic_delta"]
    absent = {key: value for key, value in ack.items() if key != "todo_source"}
    assert project(absent)[1] is False
    assert project({**ack, "agent_id": "another-agent"})[1] is False
    assert index.read_bytes() == before
