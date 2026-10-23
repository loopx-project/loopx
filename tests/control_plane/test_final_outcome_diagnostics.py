"""One final-outcome diagnosis survives public consumers without granting closure."""

from copy import deepcopy
import json

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.goals.acceptance_observation import (
    build_goal_acceptance_observation,
)
from loopx.control_plane.goals.goal_frontier.outcome_continuity import (
    acceptance_gaps_from_outcome_checkpoint,
    latest_outcome_vision_checkpoint_from_status_payload,
)
from loopx.control_plane.runtime.run_context_retention import (
    compact_goal_semantic_history,
    goal_semantic_history_from_runs,
)
from loopx.status import compact_run
from loopx.control_plane.quota.cli_projection import (
    compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.extensions.lark.presentation.projection_rows import (
    projection_rows_from_payload,
)
from loopx.quota import build_quota_should_run, render_quota_should_run_markdown

AGENT = "outcome-agent"
GOAL = "outcome-fixture"
STAMP = "2026-01-01T00:00:00Z"


def material_run():
    return {
        "goal_id": GOAL,
        "agent_id": AGENT,
        "generated_at": STAMP,
        "classification": "bounded_outcome_progress",
        "progress_scope": "agent_lane",
        "delivery_outcome": "outcome_progress",
        "agent_vision": {
            "agent_id": AGENT,
            "state": "active",
            "generated_at": STAMP,
            "vision_patch": {"vision_summary": "Verify the final outcome."},
            "path_delta": {
                "outcome": "continue",
                "evidence_refs": ["result:verified-milestone"],
                "prior_assumption": "The selected path is suitable.",
                "observed_reality": "Verified milestone supports the selected path.",
                "retained": ["Continue the selected path."],
            },
        },
        "vision_checkpoint": {
            "agent_id": AGENT,
            "satisfied": True,
            "decision": "patched",
            "generated_at": STAMP,
            "triggers": [
                {
                    "kind": "material_delivery_outcome",
                    "delivery_outcome": "outcome_progress",
                }
            ],
        },
    }


def blocked_replan_run():
    run = material_run()
    obligation = "replan-0000000000000001"
    run.update(
        generated_at="2026-01-01T00:01:00Z",
        turn_instance_id="turn-administrative-replan",
        replan_obligation_id=obligation,
        delivery_outcome="outcome_gap",
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "result_class": "blocked",
            "work_item_id": obligation,
            "blocker_id": "source-not-ready",
            "evidence_ids": ["evidence:source-readback"],
            "fingerprint": "source-readback-fingerprint",
        },
        autonomous_replan_ack={
            "schema_version": "autonomous_replan_ack_v0",
            "source": "refresh_state",
            "recorded": True,
            "semantic_delta": {
                "schema_version": "replan_semantic_delta_v0",
                "accepted": True,
                "obligation_id": obligation,
                "observation_fingerprint": "source-readback-fingerprint",
                "outcomes": ["new_concrete_blocker"],
                "satisfying_outcomes": ["new_concrete_blocker"],
            },
        },
    )
    run["vision_checkpoint"].update(
        schema_version="vision_checkpoint_v0", delivery_boundary="semantic_closeout"
    )
    run["vision_checkpoint"]["triggers"][0]["delivery_outcome"] = "outcome_gap"
    return run


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("prior_gap", [False, True])
def test_administrative_replan_preserves_prior_real_outcome_checkpoint(semantic, prior_gap):
    prior = material_run()
    prior["agent_vision"]["vision_patch"]["acceptance_summary"] = "Verified milestone."
    if prior_gap:
        prior["vision_checkpoint"]["triggers"][0]["delivery_outcome"] = "outcome_gap"
    admin = blocked_replan_run()
    # The material checkpoint must survive well outside the recent display window.
    runs = [admin, *[{"agent_id": AGENT, "classification": "quota_slot_spent"}] * 100, prior]
    goal = {"id": GOAL, "latest_runs": [compact_run(run) for run in runs]}
    if semantic:
        history = goal_semantic_history_from_runs(runs)
        context = history["agents"][0]
        assert context["latest_outcome_vision_checkpoint_run"] is prior
        assert context["latest_material_milestone_run"] is prior
        assert context["latest_autonomous_replan_ack_run"] is admin
        goal["semantic_history"] = compact_goal_semantic_history(history, compact_run=compact_run)
        goal["latest_runs"] = [compact_run(admin)]
    checkpoint = latest_outcome_vision_checkpoint_from_status_payload(
        {"run_history": {"goals": [goal]}}, goal_id=GOAL, agent_id=AGENT
    )
    assert checkpoint["generated_at"] == STAMP
    gaps = acceptance_gaps_from_outcome_checkpoint(prior["agent_vision"], checkpoint)
    assert bool(gaps) is prior_gap


@pytest.mark.parametrize("invalid", ["unaccepted", "identity", "fingerprint", "evidence", "unsatisfied", "ordinary", "claimed_only"])
def test_unqualified_blocked_replan_cannot_hide_a_real_outcome_gap(invalid):
    run = blocked_replan_run()
    if invalid == "unaccepted":
        run["autonomous_replan_ack"]["semantic_delta"]["accepted"] = False
    elif invalid == "identity":
        run["vision_checkpoint"]["agent_id"] = "other-agent"
    elif invalid == "fingerprint":
        run["autonomous_replan_ack"]["semantic_delta"]["observation_fingerprint"] = "other"
    elif invalid == "evidence":
        run["progress_observation"]["evidence_ids"] = []
    elif invalid == "unsatisfied":
        run["vision_checkpoint"]["satisfied"] = False
    elif invalid == "ordinary":
        run["todo_id"] = "todo_delivery"
    else:
        run.pop("autonomous_replan_ack")
        run["administrative_blocked_replan"] = True
    history = goal_semantic_history_from_runs([run])
    assert history["agents"][0]["latest_outcome_vision_checkpoint_run"] is run


def test_administrative_history_qualification_is_batched_and_ordinary_reads_are_local(monkeypatch):
    from loopx.control_plane.work_items import autonomous_replan_ack as codec

    calls = []
    real = codec.effect_runtime_result

    def observe(method, params, **kwargs):
        calls.append((method, len(params["runs"])))
        return real(method, params, **kwargs)

    monkeypatch.setattr(codec, "effect_runtime_result", observe)
    goal_semantic_history_from_runs([material_run()] * 100)
    assert calls == []
    history = goal_semantic_history_from_runs([blocked_replan_run()] * 100 + [material_run()])
    assert calls == [("quota.settlement.read", 100)]
    assert history["agents"][0]["latest_outcome_vision_checkpoint_run"]["generated_at"] == STAMP


def test_retry_history_uses_one_batch_and_never_retimes_the_source(monkeypatch):
    from loopx.control_plane.work_items import autonomous_replan_ack as codec

    waiting = {
        "agent_id": AGENT, "todo_id": "todo_pending001", "turn_instance_id": "pending-turn",
        "delivery_outcome": "outcome_gap", "progress_observation": {"result_class": "blocked"},
        "blocked_retry": {
            "schema_version": "quota_blocked_retry_v0", "source": "turn_settlement",
            "todo_id": "todo_pending001", "observed_at": STAMP,
            "due_at": "2026-01-01T00:05:00.000001Z",
            "resume_when": "resume_at:2026-01-01T00:05:00.000001Z",
        },
    }
    original = deepcopy(waiting)
    calls = []
    real = codec.effect_runtime_result

    def observe(method, params, **kwargs):
        calls.append((method, len(params["runs"])))
        return real(method, params, **kwargs)

    monkeypatch.setattr(codec, "effect_runtime_result", observe)
    monkeypatch.setattr("loopx.control_plane.runtime.run_context_retention.now_utc_iso",
                        lambda: "2026-01-01T00:05:00Z")
    history = goal_semantic_history_from_runs([waiting] * 100 + [material_run()])
    assert calls == [("quota.settlement.read", 100)]
    assert history["active_blocked_retry_runs"] == [waiting]
    assert waiting == original
    # One microsecond later the original clock expires; it is not renewed.
    monkeypatch.setattr("loopx.control_plane.runtime.run_context_retention.now_utc_iso",
                        lambda: "2026-01-01T00:05:00.000001Z")
    assert goal_semantic_history_from_runs([waiting])["active_blocked_retry_runs"] == []


@pytest.mark.parametrize("indexes", [[True], [-1], [1], "0", None])
def test_history_transport_rejects_malformed_qualification(indexes, monkeypatch):
    from loopx.control_plane.work_items import autonomous_replan_ack as codec

    monkeypatch.setattr(codec, "effect_runtime_result", lambda *args, **kwargs: {
        "accepted_indexes": [], "active_retry_indexes": indexes,
    })
    with pytest.raises(RuntimeError, match="valid indexes"):
        codec.qualify_settlement_history([{"blocked_retry": {}}], observed_at=STAMP)


def decision():
    status = quota_status_payload(
        goal_id=GOAL,
        status="active",
        recommended_action="Continue scoped verification.",
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_verify",
                text="Continue scoped verification.",
                task_class="advancement_task",
                action_kind="implement",
                claimed_by=AGENT,
            )
        ],
        claim_scope_agent_id=AGENT,
        latest_runs=[material_run()],
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT]},
    )
    return build_quota_should_run(
        status,
        goal_id=GOAL,
        agent_id=AGENT,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
    )


@pytest.mark.parametrize(
    "failure", ["stale", "unsatisfied", "path", "evidence", "outcome_gap"]
)
def test_other_failed_components_are_not_misdiagnosed_as_claim_only(failure):
    run = material_run()
    vision, checkpoint = run["agent_vision"], run["vision_checkpoint"]
    if failure == "stale":
        checkpoint["generated_at"] = "2025-12-31T00:00:00Z"
    elif failure == "unsatisfied":
        checkpoint["satisfied"] = False
    elif failure == "path":
        vision["path_delta"]["outcome"] = "wait"
    elif failure == "evidence":
        vision["path_delta"]["evidence_refs"] = []
    else:
        checkpoint["triggers"][0]["delivery_outcome"] = "outcome_gap"
    gap = acceptance_gaps_from_outcome_checkpoint(vision, checkpoint)[0]
    assert gap["reason_code"] == "final_outcome_checkpoint_incomplete"
    assert sum(not passed for passed in gap["component_checks"].values()) == 2


@pytest.mark.parametrize("claim", [None, "", "   "])
def test_blank_claims_are_missing_and_restoring_claim_clears_only_this_guard(claim):
    run = material_run()
    vision, checkpoint = run["agent_vision"], run["vision_checkpoint"]
    vision["vision_patch"]["acceptance_summary"] = claim
    assert (
        acceptance_gaps_from_outcome_checkpoint(vision, checkpoint)[0]["reason_code"]
        == "final_outcome_claim_missing"
    )
    vision["vision_patch"]["acceptance_summary"] = (
        "Final outcome verified by result:verified-milestone."
    )
    assert acceptance_gaps_from_outcome_checkpoint(vision, checkpoint) == []


def test_same_diagnosis_reaches_cli_managed_turn_frontend_and_lark():
    guard = decision()
    assert guard["goal_frontier_projection"]["replan_required"] is True
    audit = guard["vision_continuation_audit"]
    diagnostic = audit["outcome_checkpoint_diagnostics"][0]
    assert diagnostic["reason_code"] == "final_outcome_claim_missing"
    assert audit["closeout_allowed_without_evidence"] is False
    trigger = guard["autonomous_replan_obligation"]["triggers"][0]
    assert all(trigger[key] == value for key, value in diagnostic.items())
    assert (
        "acceptance_summary"
        in guard["autonomous_replan_obligation"]["recommended_action"]
    )
    compact = compact_quota_should_run_cli_payload(guard)
    assert compact["vision_continuation_audit"]["outcome_checkpoint_diagnostics"] == [
        diagnostic
    ]
    markdown = render_quota_should_run_markdown(compact)
    assert diagnostic["reason_code"] in markdown
    assert "final_outcome_claim_present=fail" in markdown
    assert diagnostic["resolution_hint"] in markdown
    envelope = build_turn_envelope(guard)
    assert envelope["contract_capsule"]["vision_continuation_audit"][
        "outcome_checkpoint_diagnostics"
    ] == [diagnostic]
    observation = build_goal_acceptance_observation(
        {"id": GOAL, "latest_runs": [material_run()]},
        {},
    )
    gap = next(row for row in observation["acceptance_gaps"] if row.get("reason_code"))
    assert all(gap[key] == value for key, value in diagnostic.items())
    _, rows, warnings = projection_rows_from_payload(
        guard,
        goal_id=GOAL,
        agent_id=AGENT,
        source_id="quota",
        include_done=False,
        limit=20,
    )
    assert warnings == []
    row = next(row for row in rows if row["action_kind"] == "outcome_checkpoint")
    assert all(row[key] == value for key, value in diagnostic.items())
    assert "final_outcome_claim_present=fail" in row["evidence"]
    _, peer_rows, _ = projection_rows_from_payload(
        guard,
        goal_id=GOAL,
        agent_id="other-agent",
        source_id="quota",
        include_done=False,
        limit=20,
    )
    assert not any(row["action_kind"] == "outcome_checkpoint" for row in peer_rows)


def test_public_cli_diagnostic_reentry_preserves_guard_identity_without_spending(
    tmp_path, capsys
):
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Active Goal\n\n## Agent Todo\n\n- [ ] Continue verification.\n"
        f"  <!-- loopx:todo todo_id=todo_verify status=open task_class=advancement_task claimed_by={AGENT} -->\n"
    )
    registry = project / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL,
                        "status": "active",
                        "domain": "software",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT],
                        },
                    }
                ]
            }
        )
    )
    runs = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    runs.parent.mkdir(parents=True)
    runs.write_text(json.dumps(material_run()) + "\n")
    # Consumer tests need an actual canonical provider; an unfenced Markdown
    # fixture now correctly fails before final-outcome diagnosis is evaluated.
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

    todos = parse_active_state_todos(state.read_text(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos["agent_todos"]["items"], handoff_mode="soft_claim", leases=[]
    ), state_path=state)
    before = deepcopy((state.read_text(), runs.read_text()))
    results = []
    for _ in range(2):
        assert (
            cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "quota",
                    "should-run",
                    "--goal-id",
                    GOAL,
                    "--agent-id",
                    AGENT,
                    "--runtime-profile",
                    "generic_cli",
                    "--turn-instance-id",
                    "claim-diagnostic-turn",
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["goal_frontier_projection"]["replan_required"] is True
        assert (
            result["vision_continuation_audit"]["outcome_checkpoint_diagnostics"][0][
                "reason_code"
            ]
            == "final_outcome_claim_missing"
        )
        results.append(result)
    assert (
        results[0]["autonomous_replan_obligation"]["obligation_id"]
        == results[1]["autonomous_replan_obligation"]["obligation_id"]
    )
    assert results[0]["quota"] == results[1]["quota"]
    assert (state.read_text(), runs.read_text()) == before

    # Follow the diagnosis through the actual write boundary, retaining the
    # valid route and refs while obeying the existing durable-field replan gate.
    from loopx.state_refresh import refresh_state_run

    corrected = material_run()["agent_vision"]
    corrected["vision_patch"]["acceptance_summary"] = (
        "Final outcome verified by result:verified-milestone."
    )
    corrected.pop("generated_at")
    corrected["path_delta"]["outcome"] = "replan"
    corrected["path_delta"]["observed_reality"] = (
        "The route and evidence are valid; add the missing final-outcome claim."
    )
    written = refresh_state_run(
        registry_path=registry,
        runtime_root_override=str(runtime),
        goal_id=GOAL,
        project=project,
        state_file=state,
        agent_id=AGENT,
        classification="bounded_outcome_progress",
        recommended_action="Continue verification.",
        delivery_outcome="outcome_progress",
        delivery_batch_scale="multi_surface",
        agent_vision_packet=corrected,
        autonomous_replan_recorded=True,
        dry_run=False,
        sync_global=False,
    )
    assert written["ok"] is True
    assert written["vision_checkpoint"]["satisfied"] is True
    assert written["agent_vision"]["path_delta"]["outcome"] == "replan"
    assert written["agent_vision"]["path_delta"]["evidence_refs"] == [
        "result:verified-milestone"
    ]
