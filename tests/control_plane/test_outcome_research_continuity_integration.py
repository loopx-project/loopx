"""Mixed Outcome/owner recovery retains one real admitted Turn and debit.

The reviewer is a controlled process over public synthetic requirements. File
and SQLite, CLI admission, evidence writeback and settlement are the real owners.
Three holds fill the detailed triggers; five Outcomes require the complete-set
checkpoint, including a later change beyond the bounded detailed triggers.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shlex
import subprocess
import sys

from test_goal_acceptance_cli import acceptance_goal as acceptance_goal, _configure


GOAL, AGENT = "goal-acceptance", "agent-a"
TURN = "turn-mixed-owner-outcome-review"
OUTCOMES = [f"tour-{index}" for index in range(5)]


def _call(run, *args, expected_code=0):
    code, result = run(*args)
    assert code == expected_code, result
    return result


def _refine(project, cli, update, operation):
    path = project / "outcome-refinement.json"
    path.write_text(json.dumps(update))
    return _call(cli, "refine", "--agent-id", AGENT, "--document", str(path),
        "--operation-id", operation, "--expected-provider-revision",
        _call(cli, "inspect")["provider_revision"], "--execute")


def _gaps(decision):
    # The five-row display is not the complete source contract. The admitted
    # triggers retain every held Todo and the complete Outcome-set checkpoint.
    return [row for row in decision["autonomous_replan_obligation"]["triggers"]
        if row["kind"] in {"goal_outcome_uncovered", "goal_acceptance_unbound"}]


def _checkpoints(gaps):
    return {(row["kind"], row["frontier_revision"]) for row in gaps}


def _spends(index):
    return [row for line in index.read_text().splitlines()
        if (row := json.loads(line)).get("classification") == "quota_slot_spent"] if index.exists() else []


def _admitted_command(command):
    tokens = shlex.split(command)
    start = next(index for index, token in enumerate(tokens) if token in {"refresh-state", "quota"})
    return [TURN if token == "${LOOPX_TURN:?}" else token for token in tokens[start:]]


def test_mixed_outcome_and_owner_ack_keeps_original_turn_after_new_generation(acceptance_goal):
    project, document_path, cli, run = acceptance_goal
    runtime = project.parent / "runtime"
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    document = json.loads(document_path.read_text())
    document["bindings"] = []
    document_path.write_text(json.dumps(document))
    _call(run, "todo", "update", "--goal-id", GOAL, "--agent-id", AGENT,
        "--todo-id", "todo_export", "--update-operation-id", "original-owner-source",
        "--note", "Preserve the export while its owner association is missing.")
    held_todos = ["todo_export"]
    for section in ("evidence", "appendix"):
        added = _call(run, "todo", "add", "--goal-id", GOAL, "--role", "agent",
            "--text", f"Produce the public tour {section}", "--claimed-by", AGENT,
            "--task-class", "advancement_task", "--action-kind", "implement",
            "--operation-id", f"retain-held-{section}")
        held_todos.append(added["todo_id"])
    owner = _configure(cli, document_path)["goal_acceptance_contract"]
    for outcome_id in OUTCOMES:
        _refine(project, cli, {"outcome_id": outcome_id, "owner_agent_id": AGENT,
            "objective": "Deliver a complete public product tour", "todo_ids": [],
            "requirements": [{"id": "opening", "description": "Keep the introduction"}],
            "source": {"ref": f"message:{outcome_id}", "summary": "A public tour needs its original introduction"}},
            f"retain-{outcome_id}")

    def guard(turn=None, **kwargs):
        return _call(run, "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
            *(("--codex-app", "--turn-instance-id", turn) if turn else ()), **kwargs)

    original = guard(TURN)
    (project.parent / "original-mixed-admission.json").write_text(json.dumps(original, indent=2))
    identity = original["heartbeat_receipt"]["settlement_identity"]
    obligation = identity["replan_obligation_id"]
    admitted_actions = original["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert identity["binding_kind"] == "autonomous_replan"
    initial_gaps = _gaps(original)
    initial_holds = [row for row in initial_gaps if row["kind"] == "goal_acceptance_unbound"]
    initial_outcomes = [row for row in initial_gaps if row["kind"] == "goal_outcome_uncovered"]
    assert len(initial_holds) == 3 and len(_checkpoints(initial_holds)) == 3
    assert {todo for row in initial_holds for todo in row["vision_todo_ids"]} == set(held_todos)
    assert initial_outcomes and len(_checkpoints(initial_outcomes)) == 1
    triggers = original["autonomous_replan_obligation"]["triggers"]
    assert len(triggers) == 4
    assert [row["kind"] for row in triggers[:3]] == ["goal_acceptance_unbound"] * 3
    assert triggers[-1]["kind"] == "goal_outcome_uncovered"
    assert _checkpoints(triggers) == _checkpoints(initial_holds + initial_outcomes)
    assert original["autonomous_replan_obligation"]["obligation_id"] == obligation
    assert original.get("selected_todo") is None
    assert not _spends(index)

    current = _call(cli, "inspect")["goal_acceptance_contract"]
    retained_outcomes = current["outcome_requirements"]["outcomes"]
    assert [row["id"] for row in retained_outcomes] == OUTCOMES
    complete_outcome_gaps = current["outcome_requirements"]["acceptance_gaps"]
    assert len(complete_outcome_gaps) == 5
    assert _checkpoints(complete_outcome_gaps) == _checkpoints(initial_outcomes)
    declaration = project / "observed-outcomes.json"
    declaration.write_text(json.dumps(current["outcome_requirements"]))
    evidence_file = project / "mixed-owner-outcome-review.json"
    # The reviewer reads the owner document and all five retained Outcomes.
    # It records the concrete gaps without binding work or running a checker.
    subprocess.run([sys.executable, "-c",
        "import json,sys; from pathlib import Path; owner=json.loads(Path(sys.argv[1]).read_text()); "
        "outcomes=json.loads(Path(sys.argv[2]).read_text())['outcomes']; "
        "assert owner['bindings']==[]; assert [o['id'] for o in outcomes]==[f'tour-{i}' for i in range(5)]; "
        "unmet={o['id']:[r['id'] for r in o['requirements'] if not r['planning_satisfied']] for o in outcomes}; "
        "assert all(ids==['opening'] for ids in unmet.values()); target=Path(sys.argv[3]); assert not target.exists(); "
        "target.write_text(json.dumps({'held_todos':json.loads(sys.argv[4]),'missing':'owner associations',"
        "'unmet_outcome_requirements':unmet},sort_keys=True))",
        str(document_path), str(declaration), str(evidence_file), json.dumps(held_todos)], check=True)
    evidence = "mixed-review-sha256:" + sha256(evidence_file.read_bytes()).hexdigest()
    vision = project / "mixed-review-vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
        "agent_id": AGENT, "state": "active", "vision_patch": {
            "acceptance_summary": "Preserve all three owner-held work items and all five original tour requirements.",
            "advancement_policy": "as_needed"},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "no_change",
            "prior_assumption": "The export work and tour introductions require distinct owner evidence.",
            "observed_reality": "Three work items have no owner association and five tour introductions have no work or evidence.",
            "retained": ["Every owner acceptance hold and original tour requirement."], "evidence_refs": [evidence]}}))
    # Use the admitted accountable review commands. An explicit outcome_gap
    # would instead take the existing administrative no-debit closeout path.
    refresh = next(command for command in admitted_actions if "refresh-state" in command)
    for placeholder, value in {"<advanced|blocked|exploration_exhausted|no_followup>": "blocked",
        "<surface-id>": "public-tours-and-export", "<hypothesis-id>": "owner-associations-and-introductions",
        "<probe-kind>": "artifact-inspection", "<evidence-id>": evidence}.items():
        refresh = refresh.replace(placeholder, value)
    refreshed = _call(run, *_admitted_command(refresh),
        "--progress-blocker-id", "owner-associations-and-introductions-missing",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    ack = refreshed["autonomous_replan_ack"]
    persisted_writeback = Path(refreshed["json_path"])
    retained_writeback = persisted_writeback.read_bytes()
    writeback = json.loads(retained_writeback)
    # The persisted run owns Agent/Turn identity; its compact ACK projection
    # carries that Agent into frontier matching. The nested ACK has no Agent.
    assert writeback["agent_id"] == AGENT
    assert writeback["settlement_identity"] == identity
    assert writeback["autonomous_replan_ack"] == ack
    assert ack["recorded"] is True and ack["semantic_delta"]["accepted"] is True
    assert ack["semantic_delta"]["obligation_id"] == obligation
    checkpoints = ack["semantic_delta"]["trigger_checkpoints"]
    assert len(checkpoints) == 4 and _checkpoints(checkpoints) == _checkpoints(triggers)
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    assert not _spends(index)

    # Change the fourth Outcome after verified writeback but before its debit.
    # Its complete-set checkpoint changes without replacing the original Turn.
    changed_outcome = OUTCOMES[3]
    assert changed_outcome not in {row.get("outcome_id") for row in initial_gaps}
    _refine(project, cli, {"outcome_id": changed_outcome,
        "requirements": [{"id": "order", "description": "Present delegation before the board"}],
        "source": {"ref": "message:tour-order", "summary": "Retain the required section order too"}},
        "retain-tour-order")
    changed = guard()
    (project.parent / "changed-mixed-frontier.json").write_text(json.dumps(changed, indent=2))
    changed_gaps = _gaps(changed)
    assert _checkpoints([row for row in changed_gaps if row["kind"] == "goal_acceptance_unbound"]) == _checkpoints(initial_holds)
    assert _checkpoints([row for row in changed_gaps if row["kind"] == "goal_outcome_uncovered"]) != _checkpoints(initial_outcomes)
    next_obligation = changed["autonomous_replan_obligation"]["obligation_id"]
    assert next_obligation != obligation

    pending = guard(TURN)
    (project.parent / "original-pending-debit.json").write_text(json.dumps(pending, indent=2))
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["autonomous_replan_obligation"]["obligation_id"] == obligation
    assert pending["replan_action_packet"]["settlement_only"] is True
    assert pending["settlement_progress"]["state"] == "spend_required"
    assert pending.get("selected_todo") is None
    assert len(pending["interaction_contract"]["cli_channel"]["next_cli_actions"]) == 1

    spend = _admitted_command(next(command for command in admitted_actions if "spend-slot" in command))
    before_refusals = index.read_bytes()
    for selector in (("--replan-obligation-id", next_obligation), ("--todo-id", "todo_export")):
        rejected = _call(run, "quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", TURN, *selector, "--slots", "1", "--source", "heartbeat", "--execute",
            expected_code=1)
        assert rejected.get("appended") is not True
        assert index.read_bytes() == before_refusals
        assert not _spends(index)
    spent = _call(run, *spend)
    assert spent["appended"] is True and spent["settlement_progress"]["state"] == "settled"
    assert len(_spends(index)) == 1
    after_spend = index.read_bytes()
    replayed = _call(run, *spend)
    assert replayed["idempotent_replay"] is True and replayed["appended"] is False
    assert index.read_bytes() == after_spend
    assert persisted_writeback.read_bytes() == retained_writeback
    settled = guard(TURN)
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled["heartbeat_receipt"]["settlement_identity"] == identity

    later = guard("turn-next-outcome-generation")
    assert later["autonomous_replan_obligation"]["obligation_id"] == next_obligation
    assert later["heartbeat_receipt"]["settlement_identity"]["replan_obligation_id"] == next_obligation
    assert later["heartbeat_receipt"]["settlement_identity"] != identity
    assert later.get("selected_todo") is None and later["should_run"] is True
    result = _call(cli, "inspect")["goal_acceptance_contract"]
    assert result["digest"] == owner["digest"] and result["revision"] == owner["revision"]
    assert result["status"] == "held" and result["verification"] is None
    assert {row["todo_id"] for row in result["tasks"] if row["state"] == "unbound"} == set(held_todos)
    outcomes = result["outcome_requirements"]["outcomes"]
    # An Outcome upsert moves the updated entry to the end. Preservation binds
    # every unique identity and its cumulative requirements across that update.
    assert len(outcomes) == len(OUTCOMES)
    assert {row["id"] for row in outcomes} == set(OUTCOMES)
    for outcome in outcomes:
        assert [row["id"] for row in outcome["requirements"]] == (["opening", "order"] if outcome["id"] == changed_outcome else ["opening"])
    assert result["outcome_requirements"]["execution_authority_granted"] is False
    assert result["outcome_requirements"]["goal_completion_assessed"] is False
    assert len(_spends(index)) == 1
