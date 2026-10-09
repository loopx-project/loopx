"""Executable planning/readback and first-write guidance on real local stores."""
from __future__ import annotations

import json
import shlex
from copy import deepcopy

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli, _run_generated_cli,
    _spend_run_count, _write_fixture,
)

BODY = "[P1] " + "Retain the original requirements and verify the result. " * 24 + "TAIL: preserve cancellation."


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("in_flight", [False, True])
def test_projected_inputs_support_real_readback_writeback_and_one_debit(tmp_path, monkeypatch, provider, in_flight):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("[P1] Validate and settle the selected delivery.", BODY).replace(
        "action_kind=validate -->", f"action_kind=validate claimed_by={AGENT_ID} -->"))
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        active, archived, _ = parse_todo_source(state.read_text(), goal=goal, state_path=state)
        rows = [{"schema_version": "todo_item_v0", **row}
                for row in [*active["agent"], *active["user"], *archived]]
        initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=rows, handoff_mode="legacy", leases=[]),
            state_path=state, provider=provider)

    def run(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    rc, planning = run("todo", "plan", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                       "--text", "Verify the implementation while retaining all requirements.")
    assert rc == 0, planning
    row = next(t for t in planning["existing_todos"] if t["todo_id"] == TODO_ID)
    assert len(row["text"]) < len(BODY)
    step = planning["ordered_steps"][-1]
    readback = step["command_template"].replace("<todo-id>", TODO_ID)
    rc, exact = _run_generated_cli(readback, registry_path=registry)
    assert rc == 0 and exact["matched"], exact
    assert exact["todo_detail_projection"]["source_complete"] is True
    assert exact["todo"]["text"] == BODY
    assert exact["todo"]["status"] == "open"
    assert exact["todo"]["claimed_by"] == AGENT_ID
    rc, missing = _run_generated_cli(readback.replace(TODO_ID, "todo_missing"), registry_path=registry)
    assert rc == 0 and missing["not_found"] and missing["todo"] is None
    assert _spend_run_count(runtime) == 0

    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", TURN_ID)
    rc, guard = run("quota", "should-run", "--codex-app", *binding, "--scan-path", str(project))
    assert rc == 0 and guard["interaction_contract"]["agent_channel"]["delivery_allowed"], guard
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    authoring = plan["ordered_steps"][1]["vision_authoring"]
    assert authoring["schema_version"] == "goal_vision_replan_contract_v0"
    assert build_turn_envelope(guard)["writeback"]["settlement_plan"] == plan
    rc, premature = run("checkpoint-context", *binding)
    assert rc != 0 and "original committed Turn writeback" in premature["error"]
    rc, no_writeback = _run_generated_cli(plan["ordered_steps"][2]["command_template"], registry_path=registry)
    assert rc != 0 and _spend_run_count(runtime) == 0, no_writeback

    if not in_flight:
        completion = plan["ordered_steps"][0]["command_template"].replace(
            "<validated evidence>", "Exact original body, status and claim verified.")
        rc, completed = _run_generated_cli(completion, registry_path=registry)
        assert rc == 0, completed

    # Author the documented shape with synthetic, locally verified facts; the
    # sample's evidence never substitutes for this fixture's actual readback.
    vision = deepcopy(authoring["minimal_example"])
    vision["vision_patch"]["acceptance_summary"] = "Exact source body and claim verified."
    vision["path_delta"].update(observed_reality="The complete current Todo matches its original body.",
                               retained=["Original requirements"], evidence_refs=["validation:exact-body"])
    path = tmp_path / "vision.json"
    refresh = plan["ordered_steps"][1]["command_template"].replace("<validated_progress>", "validated_progress").replace(
        "<scale>", "implementation").replace("<outcome>", "outcome_progress")
    if in_flight:
        refresh += " --delivery-boundary in_flight_continuation"
    refresh += f" --agent-vision-json {shlex.quote(str(path))} --no-global-sync --suppress-external-sinks"
    invalid = deepcopy(vision)
    invalid["vision_patch"]["acceptance_summary"] = "x" * (authoring["fields"]["vision_patch"]["acceptance_summary"] + 1)
    path.write_text(json.dumps(invalid))
    rc, rejected = _run_generated_cli(refresh, registry_path=registry)
    assert rc != 0 and _spend_run_count(runtime) == 0, rejected
    path.write_text(json.dumps(vision))
    rc, written = _run_generated_cli(refresh, registry_path=registry)
    assert rc == 0 and written["vision_checkpoint"]["satisfied"], written
    assert written["settlement_identity"] == plan["identity"]
    for replay in (False, True):
        rc, spent = _run_generated_cli(written["settlement_owed"]["command"], registry_path=registry)
        assert rc == 0 and spent["appended"] is not replay, spent
        assert _spend_run_count(runtime) == 1
    rc, final_todo = _run_generated_cli(readback, registry_path=registry)
    assert rc == 0 and final_todo["todo"]["status"] == ("open" if in_flight else "done")


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_todoless_periodic_review_uses_default_authoring_and_settles_once(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)

    def run(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    rc, configured = run("configure-goal", "--goal-id", GOAL_ID,
                         "--execution-replan-after-turns", "1", "--execute")
    assert rc == 0, configured
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID)
    rc, guard = run("quota", "should-run", "--codex-app", *binding, "--scan-path", str(project))
    assert rc == 0, guard
    rc, written = run("refresh-state", *binding, "--classification", "validated_progress",
                      "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
                      "--delivery-boundary", "in_flight_continuation", "--no-global-sync",
                      "--suppress-external-sinks")
    assert rc == 0, written
    rc, spent = run("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    assert rc == 0 and _spend_run_count(runtime) == 1, spent

    # Synthetic absence of runnable work; retain the real committed receipt.
    # Initialize each authority from this fixture, never rewrite a live store.
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "- [ ] [P1] Validate and settle the selected delivery.",
        "- [x] [P1] Validate and settle the selected delivery.").replace(
        f"todo_id={TODO_ID} status=open", f"todo_id={TODO_ID} status=done"))
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        active, archived, _ = parse_todo_source(state.read_text(), goal=goal, state_path=state)
        rows = [{"schema_version": "todo_item_v0", **row}
                for row in [*active["agent"], *active["user"], *archived]]
        initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=rows, handoff_mode="legacy", leases=[]),
            state_path=state, provider=provider)

    current = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--turn-instance-id", "periodic-without-todo", "--scan-path", str(project))
    rc, guard = run("quota", "should-run", "--codex-app", *current)
    assert rc == 0 and guard["selected_todo"] is None, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    writeback = guard["replan_action_packet"]["writeback_contract"]
    authoring = writeback["vision_authoring"]
    rc, detail = run("quota", "should-run", "--codex-app", *current, "--include-detail", "vision")
    assert rc == 0 and detail["replan_action_packet"]["writeback_contract"] == writeback, detail
    assert build_turn_envelope(guard)["replan_action_packet"]["writeback_contract"] == writeback
    rc, envelope = run("quota", "should-run", "--codex-app", *current, "--turn-envelope")
    assert rc == 0 and envelope["replan_action_packet"]["writeback_contract"] == writeback, envelope
    vision = deepcopy(authoring["minimal_example"])
    vision["path_delta"].update(
        outcome="replan", observed_reality="The settled fixture has no runnable Todo.",
        retained=["Original acceptance"], evidence_refs=[])
    path = tmp_path / "periodic-vision.json"
    command = shlex.split(guard["interaction_contract"]["cli_channel"]["next_cli_actions"][0])
    command = command[command.index("refresh-state"):]
    command[command.index("--agent-vision-json") + 1] = str(path)
    command += ["--no-global-sync", "--suppress-external-sinks"]
    path.write_text(json.dumps(vision))
    rc, rejected = run(*command)
    assert rc != 0 and _spend_run_count(runtime) == 1, rejected
    assert "typed semantic delta" in rejected["error"], rejected
    vision["path_delta"]["evidence_refs"] = ["validation:no-runnable-fixture"]
    path.write_text(json.dumps(vision))
    rc, accepted = run(*command)
    assert rc == 0 and accepted["settlement_identity"] == identity, json.dumps(accepted)
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    for replay in (False, True):
        rc, spent = _run_generated_cli(accepted["settlement_owed"]["command"], registry_path=registry)
        assert rc == 0 and spent["appended"] is not replay, spent
        assert _spend_run_count(runtime) == 2
