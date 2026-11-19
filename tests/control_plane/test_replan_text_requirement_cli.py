"""Canonical body edits rearm a real chain without inventing an acceptance owner."""
from __future__ import annotations

from hashlib import sha256
import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_goal_acceptance_cli import _configure
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry

GOAL, AGENT = "canonical-text-requirement", "proof-agent"
ORIGINAL_TEXT = "Check that x*x is nonnegative for the finite samples -2,-1,0,1,2."
REVISED_TEXT = "Prove that x*x is nonnegative for every integer, beyond the finite tested samples."


def _fixture(tmp_path, monkeypatch, provider, enabled_uncovered):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = tmp_path / "project", tmp_path / "runtime", tmp_path / "registry.json"
    project.mkdir()
    state = project / "state.md"
    state.write_text("---\nstatus: active\n---\n# Integer research\n\n## Agent Todo\n")
    write_fixture_registry(project=project, runtime_root=runtime, registry_path=registry,
        goal_id=GOAL, domain="synthetic-proof", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=[AGENT])
    todos = [{"schema_version": "todo_item_v0", "todo_id": f"todo_case_{index:02}",
        "role": "agent", "status": "open", "done": False, "task_class": "advancement_task",
        "claimed_by": AGENT, "text": ORIGINAL_TEXT if index == 0 else f"Validate independent case {index}",
        "action_kind": "validate", "target_key": f"integer-case:{index}", "index": index + 1,
        "source_section": "Agent Todo", "archive_state": "active", "priority": "P1",
        "updated_at": "2026-09-01T00:00:00Z"} for index in range(16)]
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode="soft_claim"), state_path=state, provider=provider)

    def run(*arguments):
        code, payload = run_json_cli_result(*arguments, registry_path=registry, runtime_root=runtime)
        if code != 0:
            (tmp_path / "failed-command.json").write_text(json.dumps({"arguments": arguments, "result": payload}, indent=2))
        assert code == 0, payload
        return payload

    if enabled_uncovered:
        document = tmp_path / "acceptance.json"
        document.write_text(json.dumps({"scope": {"kind": "selected_work", "todo_ids": ["todo_case_15"]},
            "objective": "Validate only independent case 15", "non_goals": ["No acceptance claim for case 0"],
            "criteria": [{"id": "independent", "description": "Independent fixture result exists",
                "validation_argv": [sys.executable, "-c", "from pathlib import Path; assert Path('case-15.txt').exists()"],
                "validation_timeout_seconds": 5}],
            "bindings": [{"todo_id": "todo_case_15", "criterion_ids": ["independent"]}]}))

        def acceptance(*arguments):
            return 0, run("goal-acceptance", *arguments, "--goal-id", GOAL)
        configured = _configure(acceptance, document)["goal_acceptance_contract"]
        assert configured["status"] == "unverified"
        assert [criterion["id"] for criterion in configured["criteria"]] == ["independent"]
        assert configured["scope"]["todo_ids"] == ["todo_case_15"]
    return project, runtime, registry, run


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("enabled_uncovered", [False, True], ids=["disabled", "uncovered"])
@pytest.mark.parametrize("settled", [False, True], ids=["unsettled", "settled"])
def test_body_change_rearms_but_display_edits_preserve_the_original_chain(
    tmp_path, monkeypatch, provider, enabled_uncovered, settled,
):
    project, runtime, _registry, run = _fixture(tmp_path, monkeypatch, provider, enabled_uncovered)

    def quota(*extra):
        return run("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, *extra)

    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    before_observation = index.read_bytes() if index.exists() else b""
    original = quota()["autonomous_replan_obligation"]
    assert [trigger["kind"] for trigger in original["triggers"]] == ["long_todo_chain"]
    original_id = original["obligation_id"]
    spend = None
    if settled:
        turn = "turn-finite-sample-review"
        bound = quota("--turn-instance-id", turn)
        assert bound["autonomous_replan_obligation"]["obligation_id"] == original_id
        # A hard-replan recommendation initially carries planning context only.
        # Reconcile its same Turn through the public guard before settling the
        # explicitly selected existing Todo, as the receipt requires.
        assert bound["heartbeat_receipt"]["settlement_binding_owed"] is True
        bound = quota("--turn-instance-id", turn, "--todo-id", "todo_case_00")
        assert bound["heartbeat_receipt"]["settlement_identity"]["todo_id"] == "todo_case_00"
        artifact = project / "finite-sample-result.json"
        subprocess.run([sys.executable, "-c",
            "import json,sys;from pathlib import Path;xs=[-2,-1,0,1,2];"
            "Path(sys.argv[1]).write_text(json.dumps({'samples':xs,'nonnegative':[x*x>=0 for x in xs],"
            "'universal_proof':False},sort_keys=True))", str(artifact)], check=True)
        assert json.loads(artifact.read_text())["universal_proof"] is False
        evidence = "finite-result:" + sha256(artifact.read_bytes()).hexdigest()
        vision = project / "finite-review-vision.json"
        vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "agent_id": AGENT,
            "state": "active", "vision_patch": {"acceptance_summary": ORIGINAL_TEXT, "advancement_policy": "as_needed"},
            "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
                "prior_assumption": "The finite cases required an independent calculation.",
                "observed_reality": "The five calculations pass; no universal proof was produced.",
                "retained": ["The remaining independent case work"], "evidence_refs": [evidence]}}))
        binding = ("--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn,
            "--todo-id", "todo_case_00")
        result = run("refresh-state", *binding, "--progress-scope", "agent_lane",
            "--classification", "finite_sample_review", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_progress", "--progress-result-class", "advanced",
            "--progress-probe-kind", "independent-finite-calculation", "--progress-surface-id", "integer-square",
            "--progress-evidence-id", evidence, "--agent-vision-json", str(vision),
            "--no-global-sync", "--suppress-external-sinks")
        ack = result["autonomous_replan_ack"]
        assert ack["recorded"] and ack["semantic_delta"]["accepted"]
        assert ack["semantic_delta"]["obligation_id"] == original_id
        assert "new_probe_family" in ack["semantic_delta"]["satisfying_outcomes"]
        spend = ("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
        assert run(*spend)["appended"] is True
        assert quota().get("autonomous_replan_obligation") is None
    else:
        # These are real native observations, not Host attempt receipts. Reads
        # must retain the obligation; external-worker dispatch/debounce has a
        # separate owner and cannot be qualified by this continuity test.
        for _ in range(2):
            observed = quota()["autonomous_replan_obligation"]
            assert observed["required"] and observed["obligation_id"] == original_id
        assert (index.read_bytes() if index.exists() else b"") == before_observation
    before = index.read_bytes() if index.exists() else b""
    for number, edit in enumerate((("--priority", "P3"), ("--note", "Same finite sample requirement"))):
        run("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", "todo_case_00",
            "--update-operation-id", f"display-{number}", *edit)
        current = quota().get("autonomous_replan_obligation")
        if settled:
            assert current is None
        else:
            assert current["required"] and current["obligation_id"] == original_id
    run("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", "todo_case_00",
        "--update-operation-id", "owner-requires-universal-proof", "--text", REVISED_TEXT)
    revised = quota()["autonomous_replan_obligation"]
    assert revised["obligation_id"] != original_id
    assert revised["triggers"][0]["frontier_owned_identity"] != original["triggers"][0]["frontier_owned_identity"]
    if settled:
        assert revised["rearmed_after_obligation_id"] == original_id
        assert run(*spend)["appended"] is False
    for _ in range(2):
        current = quota()["autonomous_replan_obligation"]
        assert current["required"] and current["obligation_id"] == revised["obligation_id"]
    assert (index.read_bytes() if index.exists() else b"") == before
    rows = [json.loads(line) for line in before.decode().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == int(settled)
