from __future__ import annotations

from loopx.control_plane.goals.goal_frontier import (
    align_autonomous_replan_guidance_with_acceptance_policy,
    autonomous_replan_scope_decision,
    compact_replan_obligation,
)
from loopx.control_plane.goals.goal_frontier.ack_policy import (
    autonomous_replan_ack_satisfies_obligation,
)
from loopx.control_plane.work_items.progress_observation import (
    build_replan_action_packet,
    build_replan_context,
)
from loopx.control_plane.work_items.autonomous_replan_obligation import (
    build_autonomous_replan_obligation_payload,
)
from loopx.status import (
    DEAD_MONITOR_REPEAT_THRESHOLD,
    build_autonomous_replan_obligation,
)


def _obligation(evidence: list[dict[str, object]]) -> dict:
    obligation = build_autonomous_replan_obligation(evidence, agent_todos=None)
    assert obligation is not None, obligation
    return obligation


def test_generic_stall_obligation_carries_novelty_guidance() -> None:
    obligation = _obligation(
        [
            {
                "kind": "typed_progress_repeat",
                "progress_fingerprint": "fingerprint-repeat",
                "progress_baseline": {
                    "schema_version": "typed_progress_observation_v0",
                    "result_class": "unchanged",
                    "surface_id": "surface-existing",
                    "fingerprint": "fingerprint-repeat",
                },
            }
        ]
    )

    policy = obligation["replan_novelty_policy"]
    assert policy["schema_version"] == "replan_evidence_delivery_policy_v0"
    assert policy["evidence_source"] == "compact_run_history"
    assert policy["delivery"] == "host_projected"
    assert policy["writeback"] == "typed_semantic_delta"

    action = obligation["recommended_action"]
    assert "host-projected coverage ledger" in action
    assert "typed semantic delta" in action
    assert "agent_todo_writeback_required" not in obligation


def test_dead_monitor_obligation_reuses_the_same_repair_delta_contract() -> None:
    obligation = _obligation(
        [
            {
                "kind": "dead_monitor_repeat",
                "monitor_target_id": "stable-monitor-target",
                "run_count": DEAD_MONITOR_REPEAT_THRESHOLD,
                "threshold": DEAD_MONITOR_REPEAT_THRESHOLD,
            }
        ]
    )

    policy = obligation["replan_novelty_policy"]
    assert policy["evidence_source"] == "compact_run_history"
    assert policy["writeback"] == "typed_semantic_delta"
    action = obligation["recommended_action"]
    assert "resolve a dead monitor loop" in action
    assert "host-projected coverage ledger" in action


def test_periodic_review_obligation_reuses_the_same_preflight_contract() -> None:
    obligation = _obligation(
        [
            {
                "kind": "periodic_review_due",
                "section": "run_history",
                "text": "periodic review threshold reached",
            }
        ]
    )

    policy = obligation["replan_novelty_policy"]
    assert policy["evidence_source"] == "compact_run_history"
    action = obligation["recommended_action"]
    assert "bounded autonomous periodic review" in action
    assert "host-projected coverage ledger" in action


def test_aligned_repeat_until_closed_guidance_keeps_novelty_hint() -> None:
    obligation = _obligation(
        [
            {
                "kind": "blocked_successor_no_progress_repeat",
                "section": "run_history",
                "text": "exact blocked successor waits repeated",
                "frontier_identity": "stable-frontier",
            }
        ]
    )
    aligned = align_autonomous_replan_guidance_with_acceptance_policy(
        obligation,
        acceptance_gaps=[
            {
                "kind": "vision_acceptance_gap",
                "advancement_policy": "repeat_until_closed",
            }
        ],
    )
    assert aligned is not None
    assert "maintenance-only continuation does not satisfy" in aligned[
        "recommended_action"
    ]
    assert "host-projected coverage ledger" in aligned["recommended_action"]
    assert aligned["replan_novelty_policy"]["writeback"] == (
        "typed_semantic_delta"
    )


def test_compact_replan_obligation_keeps_only_authoritative_seam_refs() -> None:
    obligation = _obligation(
        [
            {
                "kind": "typed_progress_repeat",
                "progress_fingerprint": "fingerprint-repeat",
            }
        ]
    )
    compact = compact_replan_obligation(obligation)
    assert compact["replan_novelty_policy"] == {
        "evidence_source": "compact_run_history",
        "delivery": "host_projected",
        "writeback": "typed_semantic_delta",
    }


def test_payload_builder_defaults_to_novelty_guidance_and_policy() -> None:
    payload = build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0",
        stall_threshold=1,
        trigger_count=1,
        triggers=[],
        guidance_actions=["create_successor"],
        todo_actions=[],
        stop_condition="stop on owner-only authority",
        recommended_action="run a bounded frontier replan",
    )

    assert "host-projected coverage ledger" in payload["recommended_action"]
    policy = payload["replan_novelty_policy"]
    assert policy["evidence_source"] == "compact_run_history"
    assert policy["delivery"] == "host_projected"
    assert policy["writeback"] == "typed_semantic_delta"


def test_payload_builder_owns_rearm_lineage_field() -> None:
    payload = build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0",
        stall_threshold=1,
        trigger_count=1,
        triggers=[{"kind": "vision_acceptance_gap"}],
        guidance_actions=["create_successor"],
        todo_actions=[],
        stop_condition="stop on owner-only authority",
        recommended_action="run a bounded frontier replan",
        rearmed_after_obligation_id="replan-0123456789abcdef",
    )

    assert payload["rearmed_after_obligation_id"] == "replan-0123456789abcdef"
    assert payload["obligation_id"] != "replan-0123456789abcdef"


def test_payload_builder_replaces_conflicting_policy_extra_fields() -> None:
    payload = build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0",
        stall_threshold=1,
        trigger_count=1,
        triggers=[],
        guidance_actions=[],
        todo_actions=[],
        stop_condition="stop on owner-only authority",
        recommended_action="run a bounded replan",
        extra_fields={
            "replan_novelty_policy": {
                "schema_version": "obsolete_policy",
                "review_evidence_log": True,
                "prefer_unattempted_direction": True,
                "repeated_blocker_restatement_rejected": True,
                "no_new_direction_closure": ["watch_lane_expiry"],
            }
        },
    )

    assert payload["replan_novelty_policy"] == {
        "schema_version": "replan_evidence_delivery_policy_v0",
        "evidence_source": "compact_run_history",
        "delivery": "host_projected",
        "writeback": "typed_semantic_delta",
    }


def test_novelty_policy_materializes_host_context_and_action_packet() -> None:
    obligation = _obligation(
        [{"kind": "periodic_review_due", "source": "fixture"}]
    )
    context = build_replan_context(
        obligation,
        goal_id="replan-goal",
        agent_id="codex-replan",
        newest_first_runs=[],
    )
    enriched = {**obligation, "replan_context": context}
    action = build_replan_action_packet(enriched)

    assert context["evidence_source"] == "compact_run_history"
    assert context["delivery"] == "host_projected"
    assert action["obligation_id"] == obligation["obligation_id"]
    assert action["required_outcome"] == "semantic_delta"


def test_semantic_ack_is_bound_to_the_exact_rotated_obligation() -> None:
    old_obligation = _obligation(
        [{"kind": "periodic_review_due", "source": "old-periodic-review"}]
    )
    current_obligation = _obligation(
        [{"kind": "vision_acceptance_gap", "source": "current-vision"}]
    )
    old_ack = {
        "recorded": True,
        "semantic_delta": {
            "schema_version": "replan_semantic_delta_v0",
            "accepted": True,
            "obligation_id": old_obligation["obligation_id"],
            "outcomes": ["fresh_vision_path_outcome"],
        },
    }

    assert autonomous_replan_ack_satisfies_obligation(
        old_ack,
        replan_obligation=current_obligation,
        acceptance_gaps=current_obligation["triggers"],
    ) is False

    current_ack = {
        **old_ack,
        "semantic_delta": {
            **old_ack["semantic_delta"],
            "obligation_id": current_obligation["obligation_id"],
        },
    }
    assert autonomous_replan_ack_satisfies_obligation(
        current_ack,
        replan_obligation=current_obligation,
        acceptance_gaps=current_obligation["triggers"],
    ) is True


def test_nonperiodic_obligation_identity_ignores_incidental_run_timestamps() -> None:
    first = _obligation(
        [
            {
                "kind": "typed_progress_repeat",
                "progress_fingerprint": "fingerprint-repeat",
                "latest_generated_at": "2026-08-27T00:00:00Z",
                "oldest_counted_generated_at": "2026-08-26T23:59:00Z",
            }
        ]
    )
    replay = _obligation(
        [
            {
                **first["triggers"][0],
                "latest_generated_at": "2026-08-27T01:00:00Z",
                "oldest_counted_generated_at": "2026-08-27T00:59:00Z",
            }
        ]
    )

    assert replay["obligation_id"] == first["obligation_id"]


def test_semantic_terminal_ack_cannot_close_an_open_todo_succession_gap() -> None:
    obligation = _obligation(
        [{"kind": "completed_advancement_without_successor", "todo_id": "todo-1"}]
    )
    no_followup_ack = {
        "recorded": True,
        "semantic_delta": {
            "schema_version": "replan_semantic_delta_v0",
            "accepted": True,
            "obligation_id": obligation["obligation_id"],
            "outcomes": ["coverage_backed_no_followup"],
        },
    }

    assert autonomous_replan_ack_satisfies_obligation(
        no_followup_ack,
        replan_obligation=obligation,
        acceptance_gaps=[],
        todo_succession_gap_open=True,
    ) is False

    successor_ack = {
        **no_followup_ack,
        "semantic_delta": {
            **no_followup_ack["semantic_delta"],
            "outcomes": ["new_runnable_successor"],
        },
    }
    assert autonomous_replan_ack_satisfies_obligation(
        successor_ack,
        replan_obligation=obligation,
        acceptance_gaps=[],
        todo_succession_gap_open=True,
    ) is True


def test_policy_normalization_precedes_deterministic_peer_scope_selection() -> None:
    obligation = _obligation([{"kind": "periodic_review_due", "source": "fixture"}])
    registered_agents = ["codex-primary", "codex-reviewer"]

    selected = {
        scope["selected_peer_agent"]
        for agent_id in registered_agents
        if (
            scope := autonomous_replan_scope_decision(
                obligation,
                agent_id=agent_id,
                registered_agent_ids=registered_agents,
            )
        )["applies"]
    }

    assert len(selected) == 1
    assert selected <= set(registered_agents)
