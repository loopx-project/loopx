"""Executable planning/readback and first-write guidance on real local stores."""
from __future__ import annotations

import json
import re
import shlex
from copy import deepcopy
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli, _run_generated_cli,
    _spend_run_count, _write_fixture,
)
from loopx.todos import complete_goal_todo
from loopx.control_plane.work_items.replan_context_codec import (
    project_replan_context,
    replan_history_from_status,
)

BODY = "[P1] " + "Retain the original requirements and verify the result. " * 24 + "TAIL: preserve cancellation."


def _initialize_authority(
    runtime: Path,
    registry: Path,
    state: Path,
    provider: str,
) -> None:
    goal = json.loads(registry.read_text())["goals"][0]
    active, archived, _ = parse_todo_source(
        state.read_text(),
        goal=goal,
        state_path=state,
    )
    rows = [
        {"schema_version": "todo_item_v0", **row}
        for row in [*active["agent"], *active["user"], *archived]
    ]
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=rows,
            handoff_mode="soft_claim",
            leases=[],
        ),
        state_path=state,
        provider=provider,
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("in_flight", [False, True])
def test_projected_inputs_support_real_readback_writeback_and_one_debit(tmp_path, monkeypatch, provider, in_flight):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("[P1] Validate and settle the selected delivery.", BODY).replace(
        "action_kind=validate -->", f"action_kind=validate claimed_by={AGENT_ID} -->"))
    _initialize_authority(runtime, registry, state, provider)

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
    rc, guard = run("quota", "should-run", "--codex-app", *binding, )
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_todoless_periodic_review_uses_default_authoring_and_settles_once(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    _initialize_authority(runtime, registry, state, provider)

    def run(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    rc, configured = run("configure-goal", "--goal-id", GOAL_ID,
                         "--execution-replan-after-turns", "1", "--execute")
    assert rc == 0, configured
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID)
    rc, guard = run("quota", "should-run", "--codex-app", *binding, )
    assert rc == 0, guard
    rc, written = run("refresh-state", *binding, "--classification", "validated_progress",
                      "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
                      "--delivery-boundary", "in_flight_continuation", "--no-global-sync",
                      "--suppress-external-sinks")
    assert rc == 0, written
    rc, spent = run("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    assert rc == 0 and _spend_run_count(runtime) == 1, spent

    # Synthetic absence of runnable work; retain the real committed receipt and
    # close the selected Todo through the canonical public writer.
    complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=TODO_ID,
        role="agent",
        agent_id=AGENT_ID,
        evidence="Synthetic fixture completion after receipt commit.",
    )

    current = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--turn-instance-id", "periodic-without-todo", )
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_replan_authoring_example_links_actual_subsequent_work(tmp_path, monkeypatch, provider):
    """A caller following the delivered example must retain a real work link."""
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    # Fund all three admitted Turns before execution; the shared fixture's
    # ordinary two-slot window is intentionally smaller than this journey.
    initial = json.loads(registry.read_text())
    initial["goals"][0]["quota"]["allowed_slots"] = 3
    registry.write_text(json.dumps(initial))
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    _initialize_authority(runtime, registry, state, provider)

    def run(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    rc, configured = run("configure-goal", "--goal-id", GOAL_ID,
                         "--execution-replan-after-turns", "1", "--execute")
    assert rc == 0, configured
    for stage in ("initial", "review", "subsequent"):
        binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                   "--todo-id", TODO_ID, "--turn-instance-id", f"link-example-{stage}")
        rc, guard = run("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                        "--agent-id", AGENT_ID, "--turn-instance-id", f"link-example-{stage}",
                        )
        if rc == 0 and guard["interaction_contract"]["cli_channel"].get("selection_required"):
            selection = guard["interaction_contract"]["cli_channel"]["selection_command"]
            command = selection["route_prefix"] + " " + selection["command_args_template"].format(todo_id=TODO_ID)
            rc, guard = _run_generated_cli(command, registry_path=registry)
        assert rc == 0 and guard["interaction_contract"]["agent_channel"]["delivery_allowed"], (
            stage, guard.get("error"), guard.get("reason"))
        args = ["refresh-state", *binding, "--classification", "validated_progress",
                "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
                "--no-global-sync", "--suppress-external-sinks"]
        if stage == "review":
            authoring = guard["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["vision_authoring"]
            example = re.search(r"retain:todo_<id>", authoring["authoring_hint"])
            assert example, "The authoring contract must offer an executable retention example."
            vision = deepcopy(authoring["minimal_example"])
            vision["todo_delta"] = [example.group().replace("todo_<id>", TODO_ID)]
            vision["path_delta"].update(
                outcome="replan", observed_reality="Continue the admitted original work.",
                retained=["Original requirements"], evidence_refs=["validation:work-link"])
            path = tmp_path / "linked-vision.json"
            path.write_text(json.dumps(vision))
            args += ["--agent-vision-json", str(path), "--autonomous-replan-recorded",
                     "--repair-delta-kind", "goal_vision_patch"]
        else:
            args += ["--delivery-boundary", "in_flight_continuation"]
        rc, written = run(*args)
        assert rc == 0, written
        rc, spent = _run_generated_cli(written["settlement_owed"]["command"], registry_path=registry)
        assert rc == 0, spent

    # Read the real immutable CLI history, not a hand-authored decision fixture.
    status = {"registry": str(registry), "runtime_root": str(runtime), "run_history": {"runs": []}}
    context = project_replan_context(
        goal_id=GOAL_ID, agent_id=AGENT_ID, runs=(), source_status=status,
        stride_strategy="evidence_conditioned_v1")
    follow = context["decision_followthrough"]
    assert [row["todo_id"] for row in follow["planned_work"]] == [TODO_ID]
    assert follow["ordinary_work"]["recorded_count"] == 1
    evidence = follow["ordinary_work"]["evidence"][0]
    assert evidence["todo_id"] == TODO_ID
    assert follow["planned_work"][0]["ordinary_evidence_refs"] == [evidence["evidence_ref"]]
    assert follow["execution_and_goal_gain"] == "unknown"
    # Reading this view neither enables stride nor settles another Turn.
    ordinary = project_replan_context(
        goal_id=GOAL_ID, agent_id=AGENT_ID, runs=(), source_status=status, stride_strategy="off")
    assert "decision_followthrough" not in ordinary
    assert _spend_run_count(runtime) == 3

    # Preserve compatibility without turning a descriptive or unsupported
    # action into a causal work link. Mutate a copy, never the authority store.
    historical = deepcopy(replan_history_from_status(status, GOAL_ID))
    review = next(row for row in historical if row.get("turn_instance_id") == "link-example-review"
                  and row.get("agent_vision"))
    review["agent_vision"]["todo_delta"] = [f"action:{TODO_ID}", "Keep investigating."]
    unlinked = project_replan_context(
        goal_id=GOAL_ID, agent_id=AGENT_ID, runs=historical,
        history_newest_first=False, stride_strategy="evidence_conditioned_v1")
    assert unlinked["decision_followthrough"]["planned_work"] == []
    assert "ordinary_work" not in unlinked["decision_followthrough"]
