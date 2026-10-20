"""Held Goal Acceptance is agent-scoped recovery evidence, never a silent rebind."""

import pytest

from loopx.control_plane.goals.goal_frontier.acceptance import acceptance_gaps_from_held_goal_binding

from loopx.control_plane.work_items.progress_observation import required_semantic_outcomes

from loopx.control_plane.goals.goal_frontier import (
    build_goal_frontier_projection_context_from_status,
    derive_goal_frontier_replan_obligation_from_summaries,
)


def _summary(*, ready_alternative: bool = False):
    executable = (
        [{"todo_id": "todo_ready", "role": "agent", "status": "open",
          "task_class": "advancement_task", "claimed_by": "agent-a"}]
        if ready_alternative else []
    )
    return {
        "open_count": 1 + len(executable),
        "current_agent_claimed_advancement_count": 1 + len(executable),
        "executable_backlog_items": executable,
        "first_executable_items": executable,
        "unclaimed_priority_open_items": [],
        "claim_scope": {"other_agent_claimed_items": []},
        "goal_acceptance_contract": {
            "criteria": [{"id": "delivery", "description": "Retain the delivery result"}],
            "tasks": [
                {"todo_id": "todo_stale", "state": "stale", "applicable": True},
            ],
        },
    }


def _source():
    return [
        {"todo_id": "todo_stale", "role": "agent", "status": "open",
         "task_class": "advancement_task", "claimed_by": "agent-a",
         "updated_at": "2026-09-23T08:02:52Z"},
        {"todo_id": "todo_unbound", "role": "agent", "status": "open",
         "task_class": "advancement_task", "claimed_by": "agent-a"},
    ]


@pytest.mark.parametrize("readback", ["legacy", "current"])
def test_stale_binding_routes_to_bounded_replan_only_when_frontier_is_empty(readback):
    summary = _summary()
    if readback == "legacy":
        summary["goal_acceptance_contract"]["enabled"] = True
    gaps = acceptance_gaps_from_held_goal_binding(summary, _source(), agent_id="agent-a")
    assert [gap["vision_todo_ids"] for gap in gaps] == [["todo_stale"]]
    assert gaps[0]["generated_at"] == "2026-09-23T08:02:52Z"
    assert len(gaps[0]["frontier_revision"]) == 64
    assert "todo_unbound" not in gaps[0]["resolution_hint"]
    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
        work_lane_contract=None, agent_id="agent-a", existing_replan_obligation=None,
        acceptance_gaps=gaps,
    )
    assert obligation is not None
    assert obligation["triggers"][0]["kind"] == "goal_acceptance_stale"
    assert "todo_stale" in obligation["recommended_action"]
    assert required_semantic_outcomes(obligation) == [
        "new_runnable_successor", "new_concrete_blocker",
    ]

    with_alternative = _summary(ready_alternative=True)
    if readback == "legacy":
        with_alternative["goal_acceptance_contract"]["enabled"] = True
    assert derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0}, agent_todo_summary=with_alternative,
        work_lane_contract=None, agent_id="agent-a", existing_replan_obligation=None,
        acceptance_gaps=acceptance_gaps_from_held_goal_binding(
            with_alternative, _source(), agent_id="agent-a"),
    ) is None


def test_stale_binding_cannot_replan_another_agents_work_or_disabled_contract():
    assert acceptance_gaps_from_held_goal_binding(_summary(), _source(), agent_id="agent-b") == []
    disabled = _summary()
    disabled["goal_acceptance_contract"]["enabled"] = False
    assert acceptance_gaps_from_held_goal_binding(disabled, _source(), agent_id="agent-a") == []


def test_stale_binding_reaches_quota_frontier_projection():
    context = build_goal_frontier_projection_context_from_status(
        goal_id="goal-a", agent_id="agent-a", status_payload={"run_history": {"goals": []}}, item={},
        project_asset=None, user_todo_summary={"open_count": 0},
        agent_todo_summary=_summary(), agent_todo_source_items=_source(),
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        neutral_replan_ack_classifications=set(),
    )
    obligation = context["replan_obligation"]
    assert obligation is not None
    assert obligation["triggers"][0]["kind"] == "goal_acceptance_stale"
    assert obligation["triggers"][0]["vision_todo_ids"] == ["todo_stale"]
    assert obligation["triggers"][0]["frontier_revision"]


@pytest.mark.parametrize("state", ["stale", "unbound"])
def test_older_vision_ack_cannot_suppress_newer_binding_hold(state):
    summary = _summary()
    summary["goal_acceptance_contract"]["tasks"][0]["state"] = state
    kind = f"goal_acceptance_{state}"
    gaps = acceptance_gaps_from_held_goal_binding(summary, _source(), agent_id="agent-a")
    old_ack = {
        "generated_at": "2026-09-23T08:00:00Z",
        "recorded": True,
        "delta_contract": {"delta_kinds": ["goal_vision_patch"]},
        "semantic_delta": {
            "accepted": True,
            "outcomes": ["new_runnable_successor"],
            "trigger_kinds": [kind],
            "trigger_checkpoints": [{
                "kind": kind,
                "frontier_revision": gaps[0]["frontier_revision"],
            }],
            "satisfying_outcomes": ["new_runnable_successor"],
        },
    }

    def derive(ack):
        return derive_goal_frontier_replan_obligation_from_summaries(
            user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
            work_lane_contract=None, agent_id="agent-a",
            existing_replan_obligation=None, acceptance_gaps=gaps,
            latest_replan_ack=ack,
        )

    assert derive(old_ack) is not None
    assert derive({**old_ack, "generated_at": "2026-09-23T08:03:00Z",
                   "semantic_delta": {**old_ack["semantic_delta"],
                                      "trigger_kinds": ["vision_acceptance_gap"]}}) is not None
    assert derive({**old_ack, "generated_at": "2026-09-23T08:03:00Z",
                   "semantic_delta": {**old_ack["semantic_delta"],
                                      "trigger_checkpoints": [{"kind": kind,
                                                               "frontier_revision": "other-todo"}]}}) is not None
    assert derive({**old_ack, "generated_at": "2026-09-23T08:03:00Z"}) is None
    assert derive({**old_ack, "generated_at": "2026-09-23T08:03:00Z",
                   "semantic_delta": {**old_ack["semantic_delta"], "outcomes": []}}) is not None


def test_unbound_binding_routes_to_replan_without_granting_execution():
    summary = _summary()
    summary["goal_acceptance_contract"]["tasks"] = [
        {"todo_id": "todo_unbound", "state": "unbound", "applicable": True},
    ]
    source = [{**_source()[1], "updated_at": "2026-09-23T08:02:52Z"}]
    gaps = acceptance_gaps_from_held_goal_binding(summary, source, agent_id="agent-a")
    assert len(gaps) == 1
    assert gaps[0]["kind"] == "goal_acceptance_unbound"
    assert gaps[0]["vision_todo_ids"] == ["todo_unbound"]
    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
        work_lane_contract=None, agent_id="agent-a", existing_replan_obligation=None,
        acceptance_gaps=gaps,
    )
    assert obligation is not None
    assert obligation["todo_actions"] == []
    assert "update_agent_vision" not in obligation["guidance_actions"]
    assert "owner" in obligation["recommended_action"]
    assert "never" in obligation["recommended_action"]


def test_hold_checkpoints_survive_crowded_vision_trigger_projection():
    summary = _summary()
    holds = acceptance_gaps_from_held_goal_binding(summary, _source(), agent_id="agent-a")
    crowded = [{"kind": "vision_acceptance_gap", "generated_at": "2026-09-23T08:00:00Z"}] * 4
    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
        work_lane_contract=None, agent_id="agent-a", existing_replan_obligation=None,
        acceptance_gaps=crowded + holds,
    )
    assert len(obligation["triggers"]) == 3
    assert obligation["triggers"][0]["kind"] == "goal_acceptance_stale"
    assert obligation["triggers"][0]["frontier_revision"] == holds[0]["frontier_revision"]


@pytest.mark.parametrize("state", ["stale", "unbound"])
def test_accepted_hold_checkpoint_survives_only_display_or_note_updates(state):
    summary = _summary()
    contract = summary["goal_acceptance_contract"]
    contract.update(revision=1, digest="a" * 64)
    contract["tasks"][0]["state"] = state
    source = _source()
    gaps = acceptance_gaps_from_held_goal_binding(summary, source, agent_id="agent-a")
    kind = f"goal_acceptance_{state}"
    ack = {
        "generated_at": "2026-09-23T08:03:00Z", "recorded": True,
        "semantic_delta": {
            "accepted": True, "outcomes": ["new_concrete_blocker"],
            "obligation_id": "replan-1111111111111111",
            "trigger_kinds": [kind], "satisfying_outcomes": ["new_concrete_blocker"],
            "trigger_checkpoints": [{"kind": kind, "frontier_revision": gaps[0]["frontier_revision"]}],
        },
    }

    def derive(current):
        return derive_goal_frontier_replan_obligation_from_summaries(
            user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
            work_lane_contract=None, agent_id="agent-a", existing_replan_obligation=None,
            acceptance_gaps=current, latest_replan_ack=ack,
        )

    assert derive(gaps) is None
    source[0].update(text="Same held operation, renamed", priority="P3",
                     note="A rewritten description of the existing hold", updated_at="2026-09-23T08:04:00Z")
    renamed = acceptance_gaps_from_held_goal_binding(summary, source, agent_id="agent-a")
    assert renamed[0]["frontier_revision"] == gaps[0]["frontier_revision"]
    assert derive(renamed) is None
    contract.update(revision=2, digest="b" * 64)
    revised = acceptance_gaps_from_held_goal_binding(summary, source, agent_id="agent-a")
    assert revised[0]["frontier_revision"] != gaps[0]["frontier_revision"]
    assert derive(revised) is not None


@pytest.mark.parametrize("state", ["stale", "unbound"])
def test_pre_stable_hold_codec_is_derived_from_exact_current_source(state):
    import hashlib
    import json

    summary, source = _summary(), _source()
    contract = summary["goal_acceptance_contract"]
    contract.update(revision=3, digest="a" * 64)
    contract["tasks"][0]["state"] = state
    gaps = acceptance_gaps_from_held_goal_binding(summary, source, agent_id="agent-a")
    # Frozen pre-stable codec: independent of the production compatibility helper.
    prior = hashlib.sha256(json.dumps(["todo_stale", "2026-09-23T08:02:52Z", "a" * 64, 3, state],
        ensure_ascii=True, separators=(",", ":"), default=str).encode()).hexdigest()
    assert gaps[0]["legacy_frontier_revision"] == prior
    ack = {"recorded": True, "generated_at": "2026-09-23T08:03:00Z", "semantic_delta": {
        "accepted": True, "obligation_id": "replan-1111111111111111",
        "outcomes": ["new_concrete_blocker"], "satisfying_outcomes": ["new_concrete_blocker"],
        "trigger_kinds": [gaps[0]["kind"]],
        "trigger_checkpoints": [{"kind": gaps[0]["kind"], "frontier_revision": prior}]}}

    def derive():
        return derive_goal_frontier_replan_obligation_from_summaries(
            user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
            work_lane_contract=None, agent_id="agent-a", existing_replan_obligation=None,
            acceptance_gaps=acceptance_gaps_from_held_goal_binding(summary, source, agent_id="agent-a"),
            latest_replan_ack=ack)

    assert derive() is None
    contract["revision"] = 4
    assert derive() is not None
    contract["revision"] = 3
    source[0]["updated_at"] = "2026-09-23T08:04:00Z"
    assert derive() is not None  # Old timestamp-bearing proof cannot cover unseen source changes.


@pytest.mark.parametrize("basis", ["disabled", "unrelated_gap", "current_checkpoint", "unrecorded"])
def test_legacy_ack_source_lookup_is_absent_from_ordinary_or_current_paths(monkeypatch, basis):
    import loopx.control_plane.goals.goal_frontier.acceptance as owner

    summary = _summary()
    summary["goal_acceptance_contract"].update(revision=1, digest="a" * 64)
    gaps = acceptance_gaps_from_held_goal_binding(summary, _source(), agent_id="agent-a")
    ack = {"agent_id": "agent-a", "recorded": True,
           "todo_source": {"store_identity": "unused-current-source"}, "semantic_delta": {
               "accepted": True, "obligation_id": "replan-1111111111111111",
               "trigger_checkpoints": [{"kind": gaps[0]["kind"],
                                        "frontier_revision": gaps[0]["frontier_revision"]}]}}
    if basis == "disabled":
        summary["goal_acceptance_contract"]["enabled"] = False
        gaps = acceptance_gaps_from_held_goal_binding(summary, _source(), agent_id="agent-a")
    elif basis == "unrelated_gap":
        gaps = [{"kind": "vision_acceptance_gap", "frontier_revision": "other"}]
    elif basis == "unrecorded":
        ack["recorded"] = False
        ack["semantic_delta"]["trigger_checkpoints"][0]["frontier_revision"] = "b" * 64

    def unavailable(*args, **kwargs):
        raise AssertionError("No legacy source read belongs to this decision path")

    monkeypatch.setattr(owner, "effect_runtime_result", unavailable)
    assert owner.project_legacy_acceptance_hold_ack(
        ack, acceptance_gaps=gaps, goal_id="goal-a", agent_id="agent-a", runtime_root="unused",
    ) is ack
