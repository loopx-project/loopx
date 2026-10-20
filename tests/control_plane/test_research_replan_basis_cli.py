"""One semantic replan opportunity survives display edits and worker restarts."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import test_goal_acceptance_cli as acceptance_fixture
from test_goal_acceptance_cli import _configure
from test_external_scheduler_worker import _args, worker
from legacy_acceptance_checkpoint_fixture import legacy_acceptance_checkpoint_cli

acceptance_goal = acceptance_fixture.acceptance_goal


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("prior_review", ["returned", "settled"])
def test_formal_task_instruction_change_rearms_the_actual_worker(
    tmp_path, monkeypatch, provider, prior_review,
):
    from test_replan_successor_guard_reentry import _fixture, GOAL, AGENT

    todos = [{"schema_version": "todo_item_v0", "todo_id": f"todo_existing_{index:03}",
        "role": "agent", "text": "[P1] Validate the finite samples" if index == 0 else f"[P1] Validate result {index}",
        "title": "Validate the finite samples" if index == 0 else f"Validate result {index}", "priority": "P1",
        "status": "open", "done": False, "task_class": "advancement_task", "action_kind": "validate",
        "index": index + 1, "source_section": "Agent Todo", "archive_state": "active", "claimed_by": AGENT,
        "updated_at": "2026-09-01T00:00:00Z"} for index in range(15)]
    call, runtime, history = _fixture(tmp_path, monkeypatch, provider, future_monitor=False, initial_todos=todos)
    history.write_text("")
    calls = tmp_path / "review-calls"
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "scheduler.json",
        wake_cmd=shlex.join([sys.executable, "-c",
            "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
            "p.write_text((p.read_text() if p.exists() else '')+'returned without conclusion\\n')", str(calls)]),
        quota_timeout_seconds=90, wake_timeout_seconds=10)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    turn = "turn-original-task-review"
    guard_args = ("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT)
    initial = call(*guard_args, "--turn-instance-id", turn)
    original = initial["autonomous_replan_obligation"]["obligation_id"]
    assert initial["effective_action"] == "autonomous_replan_required"
    if prior_review == "returned":
        assert worker.run_worker(args) == 0
    else:
        selected = call(*guard_args, "--turn-instance-id", turn, "--todo-id", "todo_existing_000")
        identity = selected["heartbeat_receipt"]["settlement_identity"]
        assert identity["todo_id"] == "todo_existing_000"
        vision = tmp_path / "task-review.json"
        vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "agent_id": AGENT,
            "state": "active", "vision_patch": {"acceptance_summary": "Retain the requested independent results.",
                "advancement_policy": "as_needed"}, "path_delta": {"schema_version": "goal_path_delta_v0",
                "outcome": "continue", "prior_assumption": "The finite sample review is the current first task.",
                "observed_reality": "The current canonical task still requests finite samples.",
                "retained": ["The requested work and its original limits."],
                "evidence_refs": ["evidence:current-canonical-task-review"]}}))
        binding = ("--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn,
                   "--todo-id", identity["todo_id"])
        refreshed = call("refresh-state", *binding, "--progress-scope", "agent_lane",
            "--classification", "task_instruction_review", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
            "--delivery-workspace-path", str(tmp_path / "project"),
            "--no-global-sync", "--suppress-external-sinks")
        assert refreshed["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
        spent = call("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
        assert spent["settlement_result"]["ok"] is True

    # Maintenance notes and timestamps do not revise the task requirement.
    # Formal task text remains material; display title/priority and the exact
    # legacy priority marker do not revise that normalized requirement.
    call("todo", "update", "--goal-id", GOAL, "--todo-id", "todo_existing_000", "--agent-id", AGENT,
        "--update-operation-id", "maintenance-note", "--note", "Read the current canonical task source.")
    unchanged = call(*guard_args).get("autonomous_replan_obligation")
    assert (unchanged or {}).get("obligation_id") == (original if prior_review == "returned" else None)
    if prior_review == "returned":
        assert worker.run_worker(args) == 0
        assert len(calls.read_text().splitlines()) == 1

    call("todo", "update", "--goal-id", GOAL, "--todo-id", "todo_existing_000", "--agent-id", AGENT,
        "--update-operation-id", "formal-instruction-change", "--text",
        "Prove the result for all integers and return its exact certificate; finite samples are insufficient.")
    revised = call(*guard_args)
    assert revised["effective_action"] == "autonomous_replan_required"
    assert revised["autonomous_replan_obligation"]["obligation_id"] != original
    assert worker.run_worker(args) == 0
    assert worker.run_worker(args) == 0
    assert len(calls.read_text().splitlines()) == (2 if prior_review == "returned" else 1)


def test_unconfirmed_work_edits_do_not_regrant_the_same_owner_review(acceptance_goal):
    project, document_path, cli, run = acceptance_goal
    document = json.loads(document_path.read_text())
    document["bindings"] = []
    document_path.write_text(json.dumps(document))
    initial = _configure(cli, document_path)["goal_acceptance_contract"]
    calls = project / "review-calls.jsonl"
    host = project / "review-host.py"
    host.write_text("from pathlib import Path\nimport json,sys\n"
        "with Path(sys.argv[1]).open('a') as stream:\n"
        "    stream.write(json.dumps({'result':'no conclusion','provider_requests':1})+'\\n')\n")
    args = _args(fake_cli=Path(sys.executable).parent / "loopx",
        state_file=project.parent / "scheduler.json",
        wake_cmd=shlex.join([sys.executable, str(host), str(calls)]),
        quota_timeout_seconds=90, wake_timeout_seconds=30)
    args.registry, args.runtime_root = str(project.parent / "registry.json"), str(project.parent / "runtime")
    args.goal_id, args.agent_id = "goal-acceptance", "agent-a"

    def quota():
        code, packet = run("quota", "should-run", "--goal-id", args.goal_id,
                           "--agent-id", args.agent_id)
        assert code == 0, packet
        assert packet["effective_action"] == "autonomous_replan_required"
        return packet

    obligation = quota()["autonomous_replan_obligation"]["obligation_id"]
    assert worker.run_worker(args) == 0
    assert len(calls.read_text().splitlines()) == 1
    for event, update in enumerate((
        ("--text", "Rename the same unconfirmed export operation"),
        ("--priority", "P3"),
        ("--note", "Another self-description of the same inconclusive review"),
    )):
        code, changed = run("todo", "update", "--goal-id", args.goal_id,
            "--todo-id", "todo_export", "--agent-id", args.agent_id,
            "--update-operation-id", f"display-event-{event}", *update)
        assert code == 0, changed
        assert quota()["autonomous_replan_obligation"]["obligation_id"] == obligation
        assert worker.run_worker(args) == 0
        assert len(calls.read_text().splitlines()) == 1
    assert cli("inspect")[1]["goal_acceptance_contract"]["revision"] == initial["revision"]

    # A real owner revision is different from an Agent's new event id or prose.
    # It is accepted through the original CAS-protected configure operation.
    document["objective"] = "Produce an export and record its checksum under the revised requirement"
    document_path.write_text(json.dumps(document))
    revised = _configure(cli, document_path)["goal_acceptance_contract"]
    assert revised["revision"] == initial["revision"] + 1
    next_obligation = quota()["autonomous_replan_obligation"]["obligation_id"]
    assert next_obligation != obligation
    assert worker.run_worker(args) == 0
    assert worker.run_worker(args) == 0
    assert len(calls.read_text().splitlines()) == 2
    attempts = json.loads(Path(args.state_file).read_text())["replan_attempts"]
    assert [attempt["obligation_id"] for attempt in attempts] == [obligation, next_obligation]
    assert all(attempt["status"] == "returned" for attempt in attempts)


@pytest.mark.parametrize("checkpoint_codec", ["stable", "pre_stable", "pre_stable_unwritten"])
def test_settled_owner_hold_stays_acknowledged_after_display_edits(acceptance_goal, checkpoint_codec):
    project, document_path, cli, run = acceptance_goal
    current_run = run
    historical_codec = checkpoint_codec != "stable"
    if historical_codec:
        # Exercise real CLI writes/readback with only the source checkpoint
        # codec pinned to the pre-stable algorithm. No receipt/ACK is forged.
        run = legacy_acceptance_checkpoint_cli(project)
    document = json.loads(document_path.read_text())
    document["bindings"] = []
    document_path.write_text(json.dumps(document))
    code, noted = run("todo", "update", "--goal-id", "goal-acceptance", "--agent-id", "agent-a",
        "--todo-id", "todo_export", "--update-operation-id", "original-review-source",
        "--note", "Record the current export before its original owner review.")
    assert code == 0, noted
    original = _configure(cli, document_path)["goal_acceptance_contract"]
    goal, agent, turn = "goal-acceptance", "agent-a", "turn-original-owner-hold"
    code, guard = run("quota", "should-run", "--goal-id", goal, "--agent-id", agent,
                      "--turn-instance-id", turn)
    assert code == 0 and guard["effective_action"] == "autonomous_replan_required", guard
    obligation = guard["autonomous_replan_obligation"]
    code, reentered = current_run("quota", "should-run", "--goal-id", goal, "--agent-id", agent,
        "--turn-instance-id", turn)
    assert code == 0, reentered
    assert reentered["autonomous_replan_obligation"]["obligation_id"] == obligation["obligation_id"]
    assert reentered["autonomous_replan_obligation"]["replan_context"]["obligation_id"] == obligation["obligation_id"]
    if checkpoint_codec == "pre_stable_unwritten":
        # The already-admitted Turn crosses the upgrade before its writeback;
        # retaining old-code writes would not verify the new recovery path.
        run = current_run
    binding = ("--goal-id", goal, "--agent-id", agent, "--turn-instance-id", turn,
               "--replan-obligation-id", obligation["obligation_id"])
    artifact = project / "owner-association-review.json"
    # The controlled review inspects the actual owner document and retains the
    # precise missing association. It neither invents a permission gate nor
    # changes the requirement to make the held work executable.
    subprocess.run([sys.executable, "-c",
        "import json,sys; from pathlib import Path; d=json.loads(Path(sys.argv[1]).read_text()); "
        "assert d['bindings']==[]; Path(sys.argv[2]).write_text(json.dumps("
        "{'todo_id':'todo_export','missing':'owner-confirmed acceptance association',"
        "'objective':d['objective']},sort_keys=True))", str(document_path), str(artifact)], check=True)
    evidence = "owner-review-sha256:" + sha256(artifact.read_bytes()).hexdigest()
    vision_path = project / "review-vision.json"
    vision_path.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "agent_id": agent, "state": "active",
        "vision_patch": {"acceptance_summary": document["objective"], "advancement_policy": "as_needed"},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "no_change",
            "prior_assumption": "The existing export still needs its original owner association.",
            "observed_reality": "The current owner document has no association for the export; preserve that exact hold.",
            "retained": ["The original contract and the existing held operation."], "evidence_refs": [evidence]},
    }))
    code, refreshed = run("refresh-state", *binding, "--progress-scope", "agent_lane",
        "--classification", "external_evidence_replan_blocked", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
        "--progress-blocker-id", "owner-association-missing", "--progress-evidence-id", evidence,
        "--agent-vision-json", str(vision_path), "--no-global-sync", "--suppress-external-sinks")
    assert code == 0 and refreshed["settlement_result"]["ok"] is True, refreshed
    ack = refreshed["autonomous_replan_ack"]
    assert ack["recorded"] is True and ack["semantic_delta"]["accepted"] is True
    assert ack["semantic_delta"]["obligation_id"] == obligation["obligation_id"]
    assert ack["semantic_delta"]["satisfying_outcomes"] == ["new_concrete_blocker"]
    spend = ("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    code, spent = run(*spend)
    assert code == 0 and spent["settlement_result"]["ok"] is True, spent
    run = current_run

    def fresh_quota():
        code, packet = run("quota", "should-run", "--goal-id", goal, "--agent-id", agent)
        assert code == 0, packet
        assert packet.get("autonomous_replan_obligation") is None, {
            key: packet.get(key) for key in ("should_run", "effective_action", "autonomous_replan_obligation")}
        assert packet["should_run"] is False, packet
        return packet

    fresh_quota()
    marker = project / "unexpected-repeated-review"
    args = _args(fake_cli=Path(sys.executable).parent / "loopx",
        state_file=project.parent / "scheduler.json", quota_timeout_seconds=90, wake_timeout_seconds=10,
        wake_cmd=shlex.join([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]))
    args.registry, args.runtime_root = str(project.parent / "registry.json"), str(project.parent / "runtime")
    args.goal_id, args.agent_id = goal, agent
    updates = (("--text", "Display rename of the same held export"),
               ("--priority", "P3"), ("--note", "A rewritten note about this unchanged hold"))
    for event, update in enumerate(updates):
        code, changed = run("todo", "update", "--goal-id", goal, "--agent-id", agent,
            "--todo-id", "todo_export", "--update-operation-id", f"after-ack-display-{event}", *update)
        assert code == 0, changed
        fresh_quota()
        assert worker.run_worker(args) == 0
    assert not marker.exists()  # D=0 and P=0 for repeated observations of the accepted original hold.
    code, replay = run(*spend)
    assert code == 0 and replay["appended"] is False and replay["idempotent_replay"] is True, replay
    current = cli("inspect")[1]["goal_acceptance_contract"]
    assert current["digest"] == original["digest"] and current["revision"] == original["revision"]
    assert current["status"] == "held"
    if historical_codec:
        document["objective"] = "Produce the export and retain its checksum under new owner criteria."
        document_path.write_text(json.dumps(document))
        _configure(cli, document_path)
        code, changed = run("quota", "should-run", "--goal-id", goal, "--agent-id", agent)
        assert code == 0 and changed["should_run"] is True, changed
        assert changed["autonomous_replan_obligation"]["obligation_id"] != obligation["obligation_id"]
