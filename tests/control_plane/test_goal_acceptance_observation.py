from __future__ import annotations

import copy
import json
from pathlib import Path

from loopx.control_plane.goals.acceptance_observation import (
    build_goal_acceptance_observation,
)
from loopx.control_plane.runtime.public_safety import validate_public_safe_value
from loopx.status import collect_status


def vision_run(
    agent: str = "agent-a",
    *,
    state: str = "active",
    acceptance: str = "Independent verification report",
) -> dict:
    return {
        "run_id": "run-evidence-1",
        "goal_id": "acceptance-demo",
        "generated_at": "2026-09-01T00:00:00+00:00",
        "classification": "state_refreshed",
        "lifecycle_flags": ["refreshed", "operator_approved"],
        "agent_id": agent,
        "agent_vision": {
            "schema_version": "agent_vision_v0",
            "agent_id": agent,
            "state": state,
            "vision_patch": {
                "acceptance_summary": acceptance,
                "replan_trigger_summary": "Verification evidence is still missing",
            },
        },
    }


def test_completed_tasks_and_historical_approval_do_not_certify_acceptance():
    goal = {"id": "acceptance-demo", "latest_runs": [vision_run()]}
    item = {
        "goal_id": goal["id"],
        "agent_todos": {"done_count": 10, "open_count": 0},
        "user_todos": {
            "items": [
                {
                    "todo_id": "todo_approval",
                    "task_class": "user_gate",
                    "done": False,
                    "status": "open",
                    "text": "Review the verification report",
                    "blocks_agent": "agent-a",
                    "claimed_by": "agent-a",
                    "decision_scope": {
                        "kind": "public_claim",
                        "granularity": "goal",
                        "scope_key": "acceptance-demo",
                    },
                }
            ]
        },
    }
    before = copy.deepcopy((goal, item))
    result = build_goal_acceptance_observation(goal, item)
    assert result["acceptance_assessed"] is False
    assert (
        result["acceptance_gaps"][0]["evidence_required"]
        == "Independent verification report"
    )
    assert result["guards"][0]["blocks_agent"] == "agent-a"
    assert result["guards"][0]["decision_scope"] == "public_claim:goal:acceptance-demo"
    assert result["guards"][0]["owner"] is None  # routing is not human authority
    assert result["historical_progress"][0]["evidence_refs"] == ["run-evidence-1"]
    assert (goal, item) == before


def test_latest_vision_is_per_agent_and_closed_lane_does_not_hide_other_lane():
    goal = {
        "id": "acceptance-demo",
        "latest_runs": [
            vision_run("agent-a", state="closed"),
            vision_run("agent-b"),
            vision_run("agent-a"),
        ],
    }
    result = build_goal_acceptance_observation(goal, {})
    assert [gap["owner"] for gap in result["acceptance_gaps"]] == ["agent-b"]


def test_missing_history_and_empty_observations_are_never_complete():
    result = build_goal_acceptance_observation(
        {"id": "acceptance-demo", "lifecycle_flags": [{}, "connected"]}, None
    )
    assert result["coverage"] == "unavailable"
    assert result["acceptance_assessed"] is False
    partial = build_goal_acceptance_observation(
        {"id": "acceptance-demo", "latest_runs": [vision_run(state="closed")]},
        {"goal_id": "acceptance-demo"},
    )
    assert partial["coverage"] == "partial"
    assert partial["acceptance_gaps"] == []
    assert partial["acceptance_assessed"] is False


def test_deferred_and_completed_gates_are_not_current_pending_gates():
    gates = [
        {
            "task_class": "user_gate",
            "todo_id": f"todo_{state}",
            "status": state,
            "done": state == "done",
            "blocks_agent": "agent-a",
        }
        for state in ["open", "blocked", "deferred", "done", "superseded"]
    ]
    result = build_goal_acceptance_observation(
        {"id": "acceptance-demo"}, {"user_todos": {"items": gates}}
    )
    assert [gate["todo_id"] for gate in result["guards"]] == [
        "todo_open",
        "todo_blocked",
    ]


def test_redaction_precedes_truncation_and_bounded_output():
    run = vision_run(acceptance="x" * 500 + " /Users/private/evidence.json")
    run["raw_log"] = "private payload"
    run["agent_vision"]["vision_patch"].pop("replan_trigger_summary")
    result = build_goal_acceptance_observation(
        {"id": "acceptance-demo", "latest_runs": [run]},
        {"recommended_action": "=".join(("token", "synthetic" * 4))},
    )
    assert result["acceptance_gaps"][0]["evidence_required"] is None
    assert result["next_action"] is None
    assert "raw_log" not in json.dumps(result)
    validate_public_safe_value(result)
    many = build_goal_acceptance_observation(
        {
            "id": "acceptance-demo",
            "latest_runs": [vision_run(f"agent-{n}") for n in range(15)],
        },
        {},
    )
    assert many["truncated"] and len(many["acceptance_gaps"]) == 12


def collect_fixture(
    root: Path,
    *,
    missing_claim: bool = False,
    display_limit: int = 0,
    delivery_outcome: str | None = None,
) -> dict:
    project, runtime = root / "project", root / "runtime"
    project.mkdir(parents=True)
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\nstatus: active\n---\n\n# Acceptance\n\n## Agent Todo\n\n- [x] Implement the change\n  <!-- loopx:todo todo_id=todo_implemented status=done task_class=advancement_task claimed_by=agent-a -->\n\n## User Todo\n\n- [ ] Review verification\n  <!-- loopx:todo todo_id=todo_review status=open task_class=user_gate blocks_agent=agent-a -->\n"
    )
    registry = project / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "acceptance-demo",
                        "status": "active",
                        "domain": "software",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                    }
                ],
            }
        )
    )
    from loopx.state_refresh import refresh_state_run

    refresh_state_run(
        registry_path=registry,
        runtime_root_override=str(runtime),
        goal_id="acceptance-demo",
        project=project,
        state_file=state,
        classification="state_refreshed",
        delivery_outcome=delivery_outcome,
        recommended_action=None,
        agent_id="agent-a",
        agent_vision_packet={
            "vision_patch": vision_run()["agent_vision"]["vision_patch"]
        },
        dry_run=False,
        sync_global=False,
    )
    if missing_claim:
        refresh_state_run(
            registry_path=registry, runtime_root_override=str(runtime),
            goal_id="acceptance-demo", project=project, state_file=state,
            classification="bounded_outcome_progress", recommended_action=None,
            agent_id="agent-b", delivery_outcome="outcome_progress",
            delivery_batch_scale="multi_surface",
            agent_vision_packet={
                "state": "active",
                "vision_patch": {"vision_summary": "Verify the final outcome."},
                "path_delta": {
                    "outcome": "continue", "prior_assumption": "The selected path is suitable.",
                    "observed_reality": "The milestone evidence supports continuing.",
                    "retained": ["Continue the selected path."],
                    "evidence_refs": ["result:verified-milestone"],
                },
            },
            dry_run=False, sync_global=False,
        )
    return collect_status(
        registry_path=registry,
        runtime_root_override=str(runtime),
        scan_roots=[],
        limit=display_limit,
        include_public_boundary_scan=False,
    )


def test_real_collection_preserves_acceptance_before_display_run_trimming(tmp_path):
    result = collect_fixture(tmp_path)
    goal = result["run_history"]["goals"][0]
    assert goal["latest_runs"] == []
    projection = goal["acceptance_observation"]
    assert projection["schema_version"] == "goal_acceptance_observation_projection_v0"
    # The full lifecycle contract now ships beside this one. They stay distinct
    # projections under distinct keys and schema versions: this observation is
    # bounded historical evidence, never the lifecycle's phase/milestone answer.
    lifecycle = goal["artifact_lifecycle"]
    assert lifecycle["schema_version"] == "goal_artifact_lifecycle_projection_v0"
    assert lifecycle["schema_version"] != projection["schema_version"]
    # Only the lifecycle projection answers phase, milestones and transitions.
    assert {"lifecycle_phase", "milestones", "next_transitions"} <= set(lifecycle)
    assert {"lifecycle_phase", "milestones", "next_transitions"}.isdisjoint(projection)
    assert (
        projection["acceptance_gaps"][0]["evidence_required"]
        == "Independent verification report"
    )
    assert projection["acceptance_gaps"][0]["owner"] == "agent-a"
    assert projection["guards"][0]["blocks_agent"] == "agent-a"
    validate_public_safe_value(projection)
    assert lifecycle["lifecycle_phase"] == "waiting_owner"
    assert {(g["id"], g["kind"]) for g in lifecycle["guards"]} == {
        ("todo_review", "owner_decision"), ("vision_acceptance_gap", "evidence_precondition"),
    }
    validate_public_safe_value(lifecycle)
    from loopx.presentation.renderers.goal_artifact_lifecycle_markdown import append_goal_artifact_lifecycle_markdown
    lines = []
    append_goal_artifact_lifecycle_markdown(lines, goal)
    assert "phase=waiting_owner" in "\n".join(lines)
    assert "vision_acceptance_gap" in "\n".join(lines)


def test_closed_stage_retains_canonical_successor_requirement():
    result = build_goal_acceptance_observation(
        {
            "id": "acceptance-demo",
            "status": "active",
            "latest_runs": [vision_run(state="vision_closed")],
        },
        {},
    )
    gap = result["acceptance_gaps"][0]
    assert gap["kind"] == "vision_successor_required"
    assert "establish a successor vision" in gap["reason"]
    assert gap["reason"] != "Verification evidence is still missing"
    assert "next bounded agent vision" in gap["evidence_required"]


def test_other_goal_run_cannot_supply_acceptance_or_historical_progress():
    result = build_goal_acceptance_observation(
        {"id": "other-goal", "latest_runs": [vision_run()]}, {}
    )
    assert result["acceptance_gaps"] == []
    assert result["historical_progress"] == []


def test_status_markdown_surfaces_gap_and_unknown_owner_without_completion_claim():
    from loopx.presentation.renderers.status_markdown import append_run_history_markdown

    goal = {"id": "acceptance-demo", "latest_runs": [vision_run("")]}
    goal["acceptance_observation"] = build_goal_acceptance_observation(goal, {})
    lines = []
    append_run_history_markdown(lines, {"goals": [goal]})
    rendered = "\n".join(lines)
    assert "acceptance observations (partial; not completion proof)" in rendered
    assert "owner=unknown: Independent verification report" in rendered
    assert "gaps=1" in rendered


def test_markdown_rejects_the_distinct_full_lifecycle_contract():
    from loopx.presentation.renderers.goal_acceptance_observation_markdown import (
        append_goal_acceptance_observation_markdown,
    )

    observation = build_goal_acceptance_observation(
        {"id": "acceptance-demo", "latest_runs": [vision_run()]}, {}
    )
    broad = {**observation, "schema_version": "goal_artifact_lifecycle_projection_v0"}
    for goal in ({"artifact_lifecycle": broad}, {"acceptance_observation": broad}):
        lines = []
        append_goal_acceptance_observation_markdown(lines, goal)
        assert lines == []
    lines = []
    append_goal_acceptance_observation_markdown(
        lines, {"acceptance_observation": observation}
    )
    assert "Independent verification report" in "\n".join(lines)


def test_lifecycle_evidence_survives_real_display_trimming(tmp_path):
    for limit in (0, 5):
        result = collect_fixture(tmp_path / str(limit), display_limit=limit, delivery_outcome="outcome_progress")
        goal = result["run_history"]["goals"][0]
        if limit == 0:
            assert goal["latest_runs"] == []
        milestones = goal["artifact_lifecycle"]["milestones"]
        assert [m["id"] for m in milestones] == ["outcome_progress"]
        assert milestones[0]["reached_evidence_refs"]


def test_lifecycle_cannot_close_over_canonical_mandatory_lane():
    from loopx.control_plane.goals.artifact_lifecycle import build_goal_artifact_lifecycle_projection
    from loopx.control_plane.work_items.work_lane import (
        lark_inbox_reply_due_work_lane_contract, observe_work_lane,
    )
    lane = lark_inbox_reply_due_work_lane_contract(
        {"capabilities": {"lark_event_inbox": {"urgency": {"reply_due": True}}}},
        current_contract=None,
    )
    projection = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={"status": "active"},
        agent_todo_summary={"open_count": 0},
        run_history={"latest_runs": [{"delivery_outcome": "primary_goal_outcome", "run_id": "run-evidence"}]},
        work_observation=observe_work_lane(lane),
    )
    assert projection["lifecycle_phase"] == "qualifying"
    assert projection["next_transitions"][0]["target_phase"] == "qualifying"
    assert projection["next_transitions"][0]["precondition"] == lane["obligation"]


def test_lifecycle_public_safety_covers_all_emitted_text():
    from loopx.control_plane.goals.artifact_lifecycle import build_goal_artifact_lifecycle_projection
    unsafe_values = [
        "C:" + chr(92) + "Users" + chr(92) + "fixture" + chr(92) + "evidence.txt",
        "/" + "etc/service/config.json", "/" + "workspace/fixture/result.json",
        "access_key=" + "synthetic" * 4, "token:" + "synthetic" * 4,
        "x" * 500 + " access_key=" + "synthetic" * 4,
        "~/evidence.txt", "path:/srv/evidence.txt",
        "file:///tmp/evidence.txt", "FILE://server/share/evidence.txt",
        "x" * 500 + " ~/evidence.txt",
    ]
    for value in unsafe_values:
        projection = build_goal_artifact_lifecycle_projection(
            goal_id="demo", goal={},
            agent_id=value, acceptance_gaps=[{"kind": "gap"}],
            run_history={"latest_runs": [{"delivery_outcome": "outcome_progress", "recommended_action": value, "evidence_ref": value}]},
        )
        validate_public_safe_value(projection)
        # An unsafe label and locator are both dropped, so the marker falls
        # back to its canonical outcome id and publishes no evidence ref.
        evidence = projection["milestones"][0]
        assert evidence["label"] == "outcome_progress"
        assert evidence["reached_evidence_refs"] == []
        assert projection["guards"][0]["agent_id"] is None
    safe = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={}, run_history={"latest_runs": [{
            "delivery_outcome": "outcome_progress", "recommended_action": "Review evidence",
            "evidence_ref": "https://example.org/evidence/42",
        }]},
    )
    assert safe["milestones"][0]["label"] == "Review evidence"
    assert safe["milestones"][0]["reached_evidence_refs"] == ["https://example.org/evidence/42"]
    validate_public_safe_value(safe)


def test_lifecycle_filters_foreign_runs_and_inactive_gates():
    from loopx.control_plane.goals.artifact_lifecycle import build_goal_artifact_lifecycle_projection
    projection = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={},
        user_todo_summary={"items": [
            {"todo_id": "gate-" + status, "task_class": "user_gate", "status": status}
            for status in ("open", "deferred", "done", "superseded")
        ]},
        run_history={"latest_runs": [{"goal_id": "other", "delivery_outcome": "outcome_progress"}]},
    )
    assert projection["milestones"] == []
    assert [g["id"] for g in projection["guards"]] == ["gate-open"]


def test_lifecycle_retains_controller_gate_without_a_todo():
    from loopx.control_plane.goals.artifact_lifecycle import attach_goal_artifact_lifecycle_projections
    payload = {
        "run_history": {"goals": [{"id": "demo"}]},
        "attention_queue": {"items": [{
            "goal_id": "demo", "waiting_on": "controller",
            "operator_question": "Approve release", "agent_todos": {"open_count": 0},
        }]},
    }
    history = {"goals": [{"id": "demo", "status": "active", "latest_runs": [
        {"delivery_outcome": "outcome_progress", "run_id": "proof"},
    ]}]}
    attach_goal_artifact_lifecycle_projections(payload, history=history)
    projection = payload["run_history"]["goals"][0]["artifact_lifecycle"]
    assert projection["lifecycle_phase"] == "waiting_owner"
    assert projection["guards"][0]["owner"] == "controller"
    assert projection["next_transitions"][0]["reason_codes"] == ["guard_open"]


def test_lifecycle_uses_evidence_retained_beyond_recent_run_window():
    from loopx.control_plane.goals.artifact_lifecycle import attach_goal_artifact_lifecycle_projections
    from loopx.control_plane.runtime.run_context_retention import goal_semantic_history_from_runs, latest_runs_with_agent_context
    proof = {"goal_id": "demo", "agent_id": "agent-a", "classification": "state_refreshed",
             "delivery_outcome": "outcome_progress", "run_id": "retained-proof"}
    runs = [{"goal_id": "demo", "agent_id": "agent-a", "classification": "monitor_poll"}
            for _ in range(20)] + [proof]
    source = {"id": "demo", "status": "active",
              "latest_runs": latest_runs_with_agent_context(runs, limit=20),
              "semantic_history": goal_semantic_history_from_runs(runs)}
    assert proof not in source["latest_runs"]
    payload = {"run_history": {"goals": [{"id": "demo", "latest_runs": []}]}}
    attach_goal_artifact_lifecycle_projections(payload, history={"goals": [source]})
    projection = payload["run_history"]["goals"][0]["artifact_lifecycle"]
    assert [m["id"] for m in projection["milestones"]] == ["outcome_progress"]
    assert projection["milestones"][0]["reached_evidence_refs"] == ["retained-proof"]
    assert projection["lifecycle_phase"] != "closing"  # missing Todo source is not zero work


def test_shared_public_safety_preserves_public_uris_and_relative_paths():
    from loopx.control_plane.runtime.public_safety import public_safe_compact_text
    for value in ("https://example.org/data/report", "docs/evidence.md", "owner authorization", "access key rotation guide"):
        validate_public_safe_value(value)
        assert public_safe_compact_text(value) == value


def test_lifecycle_gap_only_evidence_never_reads_as_closing():
    from loopx.control_plane.goals.artifact_lifecycle import build_goal_artifact_lifecycle_projection
    projection = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={"status": "active"},
        user_todo_summary={"gate_open_items": []},
        agent_todo_summary={"open_count": 0},
        run_history={"latest_runs": [{"delivery_outcome": "outcome_gap"}]},
    )
    # `outcome_gap` is material history, not a progress outcome: it stays a
    # visible unreached marker and never satisfies the closeout reading.
    assert [(m["id"], m["reached"]) for m in projection["milestones"]] == [("outcome_gap", False)]
    assert projection["lifecycle_phase"] == "qualifying"
    assert projection["next_transitions"][0]["reason_codes"] == ["milestone_unreached"]


def test_lifecycle_closeout_names_unobserved_acceptance_sources():
    from loopx.control_plane.goals.artifact_lifecycle import build_goal_artifact_lifecycle_projection
    projection = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={"status": "active"},
        user_todo_summary={"gate_open_items": []},
        agent_todo_summary={"open_count": 0},
        run_history={"latest_runs": [{"delivery_outcome": "outcome_progress"}]},
    )
    # Closing is the todo-completion reading. Without an acceptance verdict the
    # step stays inside closing and names what was not observed, rather than
    # recommending the terminal outcome with a caveat attached.
    assert projection["lifecycle_phase"] == "closing"
    transition = projection["next_transitions"][0]
    assert transition["target_phase"] == "closing"
    assert transition["reason_codes"] == ["no_open_agent_work", "acceptance_unverified"]
    assert transition["precondition"].endswith("this readout could not observe agent_vision")


def test_lifecycle_fully_observed_goal_still_needs_an_acceptance_verdict():
    """An empty `missing_sources` is not an acceptance verdict.

    With an attention item and agent vision both present the observation has
    nothing left to name, but it still reports `acceptance_assessed=False` and
    a `partial` coverage. Reading "nothing missing" as "acceptance verified"
    would recommend the terminal outcome with no acceptance behind it and no
    disclosure attached.
    """

    from loopx.control_plane.goals.acceptance_observation import (
        build_goal_acceptance_observation,
    )
    from loopx.control_plane.goals.artifact_lifecycle import (
        build_goal_artifact_lifecycle_projection,
    )

    runs = [{
        "delivery_outcome": "outcome_progress",
        "agent_id": "agent-a",
        "agent_vision": {"agent_id": "agent-a", "acceptance_met": True},
    }]
    attention = {
        "goal_id": "demo",
        "user_todos": {"gate_open_items": []},
        "agent_todos": {"open_count": 0},
    }
    observation = build_goal_acceptance_observation(
        {"id": "demo", "status": "active", "latest_runs": runs}, attention
    )
    assert observation["missing_sources"] == []
    assert observation["acceptance_assessed"] is False
    assert observation["coverage"] != "complete"

    projection = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={"id": "demo", "status": "active"},
        user_todo_summary={"gate_open_items": []},
        agent_todo_summary={"open_count": 0},
        run_history={"latest_runs": runs},
        attention_item=attention,
    )
    transition = projection["next_transitions"][0]
    assert transition["target_phase"] == "closing"
    assert transition["reason_codes"] == ["no_open_agent_work", "acceptance_unverified"]
    assert "could not observe" not in transition["precondition"]
