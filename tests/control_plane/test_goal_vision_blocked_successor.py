from __future__ import annotations

import json
from copy import deepcopy

import pytest

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
)
from loopx.cli_commands.status import attach_agent_lane_next_actions
from loopx.control_plane.goals.goal_frontier import (
    build_goal_frontier_projection_context_from_status,
    select_autonomous_replan_obligation,
)
from loopx.control_plane.runtime.agent_scoped_evidence_log import (
    build_agent_scoped_evidence_log_command,
)
from loopx.control_plane.quota.monitor_poll import build_quota_monitor_poll_event
from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.control_plane.work_items.autonomous_replan_ack import (
    latest_blocked_successor_frontier_identity,
    latest_monitor_replan_frontier_identity,
)
from loopx.control_plane.work_items.autonomous_replan_obligation import (
    ensure_replan_novelty_policy,
)
from loopx.presentation.renderers.status_markdown import render_status_markdown
from loopx.quota import build_quota_should_run, render_quota_should_run_markdown
from loopx.state_refresh import build_state_refresh_record, refresh_state_run
from loopx.status import autonomous_replan_obligation_from_runs, compact_run

GOAL_ID = "vision-blocked-successor-fixture"
AGENT_ID = "codex-side-agent"
PRIMARY_AGENT = "codex-primary-agent"
BLOCKER_ID = "todo_exact_blocker"
WAITING_ID = "todo_waiting_successor"
CROSS_DOMAIN_WAITING_ID = "todo_cross_domain_waiting"
CLAIMED_WAITING_ID = "todo_claimed_waiting"
MONITOR_ID = "todo_future_monitor"
MONITOR_BLOCKED_ID = "todo_monitor_blocked_advancement"
PROJECTED_BLOCKER_ID = "todo_projected_agent_blocker"


def _vision_run(
    *,
    state: str = "vision_drift_detected",
    missing_checkpoint: bool = False,
    advancement_policy: str = "repeat_until_closed",
    vision_todo_ids: list[str] | None = None,
) -> dict:
    run = {
        "classification": "vision_blocked_successor_fixture",
        "generated_at": "2026-07-16T00:00:00+00:00",
        "agent_id": AGENT_ID,
        "progress_scope": "agent_lane",
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "agent_id": AGENT_ID,
            "state": state,
            "todo_delta": [
                f"activate:{todo_id}"
                for todo_id in (
                    vision_todo_ids
                    if vision_todo_ids is not None
                    else [BLOCKER_ID]
                )
            ],
            "vision_patch": {
                "acceptance_summary": "Deliver the exact successor after its prerequisite clears.",
                "replan_trigger_summary": "The successor acceptance remains open.",
                "advancement_policy": advancement_policy,
            },
        },
    }
    if missing_checkpoint:
        run["vision_checkpoint"] = {
            "schema_version": "vision_checkpoint_v0",
            "agent_id": AGENT_ID,
            "required": True,
            "satisfied": False,
            "decision": "missing_required",
            "triggers": [{"kind": "material_delivery_outcome"}],
        }
    return run


def _status_payload(
    *,
    blocker_status: str = "open",
    waiting_status: str = "open",
    blocker_task_class: str = "advancement_task",
    vision_state: str = "vision_drift_detected",
    missing_checkpoint: bool = False,
    advancement_policy: str = "repeat_until_closed",
    latest_runs: list[dict] | None = None,
    extra_agent_items: list[dict] | None = None,
) -> dict:
    blocker = quota_todo_item(
        todo_id=BLOCKER_ID,
        index=1,
        text="[P0] Complete the exact prerequisite.",
        status=blocker_status,
        task_class=blocker_task_class,
        claimed_by=PRIMARY_AGENT,
        successor_todo_ids=[WAITING_ID],
    )
    waiting = quota_todo_item(
        todo_id=WAITING_ID,
        index=2,
        text="[P0] Resume the exact successor.",
        status=waiting_status,
        claimed_by=AGENT_ID,
        resume_when=f"todo_done:{BLOCKER_ID}",
    )
    agent_todos = quota_todo_summary(
        [blocker, waiting, *(extra_agent_items or [])],
        role="agent",
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Resume the exact successor after its prerequisite clears.",
        agent_todos=agent_todos,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT, AGENT_ID],
        },
        latest_runs=latest_runs
        if latest_runs is not None
        else [
            _vision_run(
                state=vision_state,
                missing_checkpoint=missing_checkpoint,
                advancement_policy=advancement_policy,
            )
        ],
    )


def _cross_domain_wait_status_payload() -> dict:
    unclaimed = quota_todo_item(
        todo_id=CROSS_DOMAIN_WAITING_ID,
        index=1,
        text="[P1] Resume a benchmark-runner task owned by another lane.",
        status="deferred",
        priority="P1",
        action_kind="benchmark_runner_external_lane",
        required_capabilities=["benchmark_runner"],
        resume_when="capacity_available:benchmark_runner",
    )
    claimed = quota_todo_item(
        todo_id=CLAIMED_WAITING_ID,
        index=2,
        text="[P2] Resume this agent's release qualification successor.",
        status="deferred",
        priority="P2",
        action_kind="release_evidence_qualification",
        claimed_by=AGENT_ID,
        required_capabilities=["benchmark_runner"],
        resume_when="capacity_available:benchmark_runner",
    )
    agent_todos = quota_todo_summary([unclaimed, claimed], role="agent")
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Wait for the current agent's exact successor.",
        agent_todos=agent_todos,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT, AGENT_ID],
            "agent_profiles": {
                AGENT_ID: {
                    "schema_version": "agent_profile_v1",
                    "agent_id": AGENT_ID,
                    "profile_role": "quality-qualification",
                    "default_task_classes": [
                        "advancement_task",
                        "continuous_monitor",
                    ],
                    "vision_requirement": "required",
                    "preferred_action_kinds": ["release_evidence_*"],
                    "avoid_action_kinds": ["benchmark_runner_*"],
                }
            },
        },
        latest_runs=[_vision_run()],
    )


def _quota(payload: dict) -> dict:
    item = payload["attention_queue"]["items"][0]
    obligation = item.get("autonomous_replan_obligation") or {}
    normalized_obligation = (
        ensure_replan_novelty_policy(obligation) if obligation else {}
    )
    obligation_id = (
        str(normalized_obligation.get("obligation_id") or "").strip() or None
    )
    run_history = payload.get("run_history") or {}
    goals = run_history.get("goals") if isinstance(run_history, dict) else None
    latest_runs = payload.get("latest_runs")
    if latest_runs is None and isinstance(goals, list) and goals:
        latest_runs = goals[0].get("latest_runs")
    acked_at = None
    for run in latest_runs or []:
        if run.get("autonomous_replan_ack") is not None:
            acked_at = run.get("generated_at")
            break
    if acked_at:
        item["evidence_log_read_receipts"] = [
            {
                "schema_version": "evidence_log_read_receipt_v0",
                "event_id": f"fixture-receipt-{acked_at}",
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "status": "completed",
                "recorded_at": acked_at,
                "command": build_agent_scoped_evidence_log_command(
                    goal_id=GOAL_ID,
                    agent_id=AGENT_ID,
                    required_read_id=obligation_id,
                ),
                "read_window": {"mode": "thin", "limit": 24},
                **(
                    {"required_read_id": obligation_id}
                    if obligation_id
                    else {}
                ),
            }
        ]
    return build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )


def _current_agent_blocker_status_payload(
    *,
    claimed_by: str = AGENT_ID,
    reason: str | None = "Candidate promotion requires controller assignment.",
    latest_runs: list[dict] | None = None,
) -> dict:
    peer_items = [
        quota_todo_item(
            todo_id=f"todo_peer_{index}",
            index=index + 1,
            text=f"[P0] Peer advancement {index}.",
            claimed_by=PRIMARY_AGENT,
        )
        for index in range(20)
    ]
    blocker = quota_todo_item(
        todo_id=PROJECTED_BLOCKER_ID,
        index=21,
        text="[P2 blocker] Wait for a qualifying advancement assignment.",
        status="blocked",
        task_class="blocker",
        priority="P2",
        claimed_by=claimed_by,
        reason=reason,
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Wait for a qualifying advancement assignment.",
        agent_todos=quota_todo_summary(
            [*peer_items, blocker],
            role="agent",
            item_limit=12,
        ),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT, AGENT_ID],
        },
        latest_runs=(
            latest_runs
            if latest_runs is not None
            else [_vision_run(vision_todo_ids=[PROJECTED_BLOCKER_ID])]
        ),
    )


def _blocked_wait_polls() -> list[dict]:
    guard = _quota(_status_payload())
    return [
        build_quota_monitor_poll_event(
            guard,
            generated_at="2026-07-16T00:02:00+00:00",
        ),
        build_quota_monitor_poll_event(
            guard,
            generated_at="2026-07-16T00:01:00+00:00",
        ),
    ]


def _generic_quiet_polls(*, target_id: str = "generic-watch-target") -> list[dict]:
    return [
        {
            "classification": "quota_monitor_poll",
            "generated_at": generated_at,
            "turn_instance_id": f"heartbeat-{generated_at}",
            "agent_id": AGENT_ID,
            "monitor_target": {
                "schema_version": "quota_monitor_target_v0",
                "target_id": target_id,
                "monitor_mode": "monitor_quiet_until_material_transition",
                "effective_action": "monitor_quiet_skip",
                "action_summary": "Wait for the bounded watch frontier.",
                "agent_id": AGENT_ID,
            },
        }
        for generated_at in (
            "2026-07-16T00:02:00+00:00",
            "2026-07-16T00:01:00+00:00",
        )
    ]


def _executed_monitor_polls(
    *,
    target_id: str = "generic-watch-target",
    agent_id: str = AGENT_ID,
    date: str = "2026-07-16",
) -> list[dict]:
    return [
        {
            "classification": "quota_monitor_poll",
            "generated_at": f"{date}T00:0{minute}:00+00:00",
            "agent_id": agent_id,
            "todo_id": MONITOR_ID,
            "target_key": "bounded-watch",
            "monitor_target": {
                "schema_version": "quota_monitor_target_v0",
                "target_id": target_id,
                "monitor_mode": "due_monitor_observed_without_material_transition",
                "effective_action": "normal_run",
                "action_summary": "Observe the due bounded watch.",
                "agent_id": agent_id,
            },
        }
        for minute in range(6, 0, -1)
    ]


def _generic_watch_ack(*, frontier_identity: str) -> dict:
    return {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:00:30+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "frontier_identity": frontier_identity,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["watch_lane_continuation"],
                "auto_evidence": [
                    {
                        "kind": "watch_lane_continuation",
                        "todo_ids": [MONITOR_ID],
                    }
                ],
            },
        },
    }


def _append_bounded_watch_todo(state_path, *, claimed_by: str) -> None:
    state_path.write_text(
        state_path.read_text(encoding="utf-8")
        + "\n## Agent Todo\n\n"
        + "- [ ] [P2] Keep the exact bounded watch active.\n"
        + "  <!-- loopx:todo "
        + f"todo_id={MONITOR_ID} status=open task_class=continuous_monitor "
        + f"claimed_by={claimed_by} target_key=bounded-watch cadence=7d "
        + "next_due_at=2099-01-01T00%3A00%3A00Z "
        + "expires_at=2099-02-01T00%3A00%3A00Z -->\n",
        encoding="utf-8",
    )


def _generic_watch_quota(
    *,
    ack: dict | None,
    advancement_policy: str = "as_needed",
    expires_at: str = "2099-02-01T00:00:00+00:00",
    target_id: str = "generic-watch-target",
    extra_agent_items: list[dict] | None = None,
) -> dict:
    monitor = quota_todo_item(
        todo_id=MONITOR_ID,
        index=1,
        text="[P1] Wait for material evidence on the bounded watch.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="bounded-watch",
        cadence="7d",
        next_due_at="2099-01-01T00:00:00+00:00",
        expires_at=expires_at,
    )
    runs = [
        *_executed_monitor_polls(target_id=target_id),
        *([ack] if ack is not None else []),
        _vision_run(
            state="conditional_monitoring",
            advancement_policy=advancement_policy,
            vision_todo_ids=[MONITOR_ID],
        ),
    ]
    agent_todos = quota_todo_summary(
        [monitor, *(extra_agent_items or [])],
        role="agent",
    )
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Wait for the bounded watch frontier.",
        agent_todos=agent_todos,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT, AGENT_ID],
        },
        latest_runs=runs,
    )
    item = payload["attention_queue"]["items"][0]
    obligation = autonomous_replan_obligation_from_runs(
        runs,
        agent_todos=item["agent_todos"],
    )
    assert obligation is not None
    item["autonomous_replan_obligation"] = obligation
    item["project_asset"]["autonomous_replan_obligation"] = obligation
    return _quota(payload)


def test_current_agent_blocker_defers_open_vision_gap_without_hiding_reason() -> None:
    guard = _quota(_current_agent_blocker_status_payload())

    assert guard["decision"] == "reassignment_required"
    assert guard["should_run"] is False
    assert guard.get("autonomous_replan_obligation") is None
    summary = guard["agent_todo_summary"]
    assert summary["current_agent_blocker_count"] == 1
    assert summary["current_agent_blocker_items"][0]["todo_id"] == (
        PROJECTED_BLOCKER_ID
    )
    frontier = guard["goal_frontier_projection"]
    assert frontier["acceptance_gaps"] == []
    assert frontier["replan_required"] is False
    assert "current_agent_blocker" in frontier["autonomy_blockers"]
    wait = frontier["vision_wait_state"]
    assert wait["reason_code"] == "current_agent_blocker"
    assert wait["selected_todo_id"] == PROJECTED_BLOCKER_ID
    assert wait["blocker_reason"] == (
        "Candidate promotion requires controller assignment."
    )
    assert wait["automatic_resume"] is False

    markdown = render_quota_should_run_markdown(guard)
    assert (
        "current_agent_blocker=1"
    ) in markdown
    assert (
        f"todo_id={PROJECTED_BLOCKER_ID} "
        "reason=Candidate promotion requires controller assignment."
    ) in markdown


def test_current_agent_blocker_outranks_exact_blocked_successor() -> None:
    blocker = quota_todo_item(
        todo_id=PROJECTED_BLOCKER_ID,
        index=3,
        text="[P2 blocker] Wait for a qualifying advancement assignment.",
        status="blocked",
        task_class="blocker",
        priority="P2",
        claimed_by=AGENT_ID,
        reason="Candidate promotion requires controller assignment.",
    )

    guard = _quota(
        _status_payload(
            extra_agent_items=[blocker],
            latest_runs=[
                _vision_run(
                    vision_todo_ids=[PROJECTED_BLOCKER_ID, BLOCKER_ID]
                )
            ],
        )
    )

    wait = guard["goal_frontier_projection"]["vision_wait_state"]
    assert wait["reason_code"] == "current_agent_blocker"
    assert wait["selected_todo_id"] == PROJECTED_BLOCKER_ID
    assert wait["automatic_resume"] is False
    assert guard["agent_scope_frontier"]["blocked_successor_wait_candidates"][0][
        "todo_id"
    ] == WAITING_ID


@pytest.mark.parametrize(
    ("claimed_by", "reason"),
    [
        (PRIMARY_AGENT, "Peer-owned blocker."),
        (AGENT_ID, None),
    ],
    ids=["other-agent", "missing-reason"],
)
def test_unscoped_or_malformed_blocker_does_not_defer_open_vision_gap(
    claimed_by: str,
    reason: str | None,
) -> None:
    guard = _quota(
        _current_agent_blocker_status_payload(
            claimed_by=claimed_by,
            reason=reason,
        )
    )

    assert guard["decision"] == "autonomous_replan_required"
    assert "vision_wait_state" not in guard["goal_frontier_projection"]
    assert guard["goal_frontier_projection"]["replan_required"] is True


def test_unbound_legacy_blocker_ack_cannot_clear_vision_replan_obligation() -> None:
    obligation = {
        "schema_version": "autonomous_replan_obligation_v0",
        "required": True,
        "agent_id": AGENT_ID,
        "triggers": [{"kind": "vision_acceptance_gap"}],
    }
    ack = {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:03:00+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["blocker"],
            },
        },
    }
    payload = _current_agent_blocker_status_payload(
        latest_runs=[
            ack,
            _vision_run(vision_todo_ids=[PROJECTED_BLOCKER_ID]),
        ]
    )
    item = payload["attention_queue"]["items"][0]
    item["autonomous_replan_obligation"] = obligation
    item["project_asset"]["autonomous_replan_obligation"] = obligation

    guard = _quota(payload)

    assert guard["decision"] == "autonomous_replan_required"
    assert guard["should_run"] is True
    assert guard["autonomous_replan_obligation"]["required"] is True
    assert guard["goal_frontier_projection"]["replan_required"] is True


def _status_payload_with_replan_runs(
    runs: list[dict],
    *,
    extra_agent_items: list[dict] | None = None,
) -> tuple[dict, dict | None]:
    payload = _status_payload(
        latest_runs=runs,
        extra_agent_items=extra_agent_items,
    )
    item = payload["attention_queue"]["items"][0]
    obligation = autonomous_replan_obligation_from_runs(
        runs,
        agent_todos=item["agent_todos"],
    )
    if obligation is not None:
        item["autonomous_replan_obligation"] = obligation
        item["project_asset"]["autonomous_replan_obligation"] = obligation
    return payload, obligation


def _quota_with_replan_runs(
    runs: list[dict],
    *,
    extra_agent_items: list[dict] | None = None,
) -> dict:
    payload, _ = _status_payload_with_replan_runs(
        runs,
        extra_agent_items=extra_agent_items,
    )
    return _quota(payload)


def _set_two_agent_replan_obligations(
    payload: dict,
    obligation: dict,
) -> tuple[dict, dict]:
    item = payload["attention_queue"]["items"][0]
    peer_obligation = {
        **obligation,
        "agent_id": PRIMARY_AGENT,
        "recommended_action": "Keep the peer lane unchanged.",
    }
    by_agent = {AGENT_ID: obligation, PRIMARY_AGENT: peer_obligation}
    item["autonomous_replan_obligations_by_agent"] = deepcopy(by_agent)
    item["project_asset"]["autonomous_replan_obligations_by_agent"] = deepcopy(
        by_agent
    )
    return item, peer_obligation


def _monitor_blocked_advancement_items(
    *,
    next_due_at: str | None,
    claimed_by: str = AGENT_ID,
) -> list[dict]:
    monitor_metadata = {
        "target_key": "future-monitor-target",
        "cadence": "10m",
    }
    if next_due_at is not None:
        monitor_metadata["next_due_at"] = next_due_at
    return [
        quota_todo_item(
            todo_id=MONITOR_ID,
            index=3,
            text="[P0] Monitor the pending external result.",
            task_class="continuous_monitor",
            claimed_by=claimed_by,
            **monitor_metadata,
        ),
        quota_todo_item(
            todo_id=MONITOR_BLOCKED_ID,
            index=4,
            text="[P0] Resume delivery after the monitor completes.",
            status="deferred",
            claimed_by=claimed_by,
            resume_when=f"todo_done:{MONITOR_ID}",
        ),
    ]


@pytest.mark.parametrize("waiting_status", ["open", "deferred"])
def test_exact_blocked_successor_defers_only_open_vision_gap(
    waiting_status: str,
) -> None:
    guard = _quota(_status_payload(waiting_status=waiting_status))

    assert guard["decision"] == "agent_scope_wait"
    assert guard["should_run"] is False
    assert guard["normal_delivery_allowed"] is False
    assert guard.get("autonomous_replan_obligation") is None
    frontier = guard["goal_frontier_projection"]
    assert frontier["acceptance_gaps"] == []
    assert frontier["replan_required"] is False
    assert "vision_blocked_successor_wait" in frontier["autonomy_blockers"]
    wait = frontier["vision_wait_state"]
    assert wait["schema_version"] == "goal_vision_wait_state_v0"
    assert wait["selected_todo_id"] == WAITING_ID
    assert wait["selected_todo_status"] == waiting_status
    assert wait["resume_when"] == f"todo_done:{BLOCKER_ID}"
    assert wait["resume_condition"]["target_todo_id"] == BLOCKER_ID
    assert wait["resume_condition"]["satisfied"] is False
    assert wait["deferred_acceptance_gap_count"] == 1
    assert wait["automatic_resume"] is True
    assert guard["vision_wait_state"] == wait
    assert guard["agent_scope_frontier"]["action"] == "agent_scope_wait"
    assert guard["agent_scope_frontier"].get("requires_replan") is not True
    assert guard["agent_scope_frontier"]["blocked_successor_wait_candidates"][0][
        "todo_id"
    ] == WAITING_ID
    assert guard["agent_lane_frontier_hint"]["reason_code"] == (
        "blocked_successor_resume_pending"
    )
    assert guard["interaction_contract"]["agent_channel"]["vision_wait_state"] == wait
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False
    cli_actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert "quota monitor-poll" in cli_actions[0]
    assert "quota should-run" in cli_actions[1]
    assert "agent_action_required=true" in guard["protocol_action_packet"]["summary"]
    cli_wait = guard["interaction_contract"]["cli_channel"]["vision_wait_state"]
    assert cli_wait["selected_todo_id"] == WAITING_ID
    assert cli_wait["automatic_resume"] is True
    assert "vision_continuation_audit" not in guard

    markdown = render_quota_should_run_markdown(guard)
    assert (
        "vision_wait_state: state=waiting "
        f"todo_id={WAITING_ID} resume_when=todo_done:{BLOCKER_ID} "
        "automatic_resume=True"
    ) in markdown


def test_unrelated_deferred_waits_do_not_hide_open_vision_gap() -> None:
    guard = _quota(_cross_domain_wait_status_payload())

    assert guard["decision"] == "autonomous_replan_required"
    assert "vision_wait_state" not in guard["goal_frontier_projection"]
    assert guard["goal_frontier_projection"]["acceptance_gaps"][0]["kind"] == (
        "vision_acceptance_gap"
    )

    status_payload = _cross_domain_wait_status_payload()
    attach_agent_lane_next_actions(status_payload, agent_id=AGENT_ID)
    item = status_payload["attention_queue"]["items"][0]
    assert "vision_wait_state" not in item["goal_frontier_projection"]
    assert item["goal_frontier_projection"]["replan_required"] is True


def test_exact_successor_lineage_excludes_unrelated_deferred_waits() -> None:
    unrelated = quota_todo_item(
        todo_id=CLAIMED_WAITING_ID,
        index=3,
        text="[P0] Wait for an unrelated release capability.",
        status="deferred",
        claimed_by=AGENT_ID,
        required_capabilities=["benchmark_runner"],
        resume_when="capacity_available:benchmark_runner",
    )

    guard = _quota(_status_payload(extra_agent_items=[unrelated]))

    wait = guard["goal_frontier_projection"]["vision_wait_state"]
    assert wait["selected_todo_id"] == WAITING_ID
    assert wait["waiting_todo_ids"] == [WAITING_ID]


def test_exact_wait_without_explicit_vision_lineage_requires_replan() -> None:
    guard = _quota(
        _status_payload(latest_runs=[_vision_run(vision_todo_ids=[])])
    )

    assert guard["decision"] == "autonomous_replan_required"
    assert "vision_wait_state" not in guard["goal_frontier_projection"]
    assert guard["goal_frontier_projection"]["replan_required"] is True


def test_two_identical_blocked_successor_waits_trigger_bounded_replan() -> None:
    polls = _blocked_wait_polls()
    target = polls[0]["monitor_target"]
    assert target["monitor_mode"] == (
        "blocked_successor_wait_without_material_transition"
    )
    assert target["frontier_identity"]
    assert polls[1]["monitor_target"]["target_id"] == target["target_id"]
    assert polls[1]["monitor_target"]["frontier_identity"] == target[
        "frontier_identity"
    ]

    quiet_monitor = quota_todo_item(
        todo_id=MONITOR_ID,
        index=3,
        text="[P2] Wait quietly for material monitor evidence.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="future-monitor-target",
        cadence="1d",
        next_due_at="2099-01-01T00:00:00+00:00",
    )
    guard = _quota_with_replan_runs(
        [*polls, _vision_run()],
        extra_agent_items=[quiet_monitor],
    )

    assert guard["decision"] == "autonomous_replan_required"
    assert guard["should_run"] is True
    obligation = guard["autonomous_replan_obligation"]
    assert obligation["stall_threshold"] == 2
    assert obligation["frontier_identity"] == target["frontier_identity"]
    assert obligation["triggers"][0]["kind"] == (
        "blocked_successor_no_progress_repeat"
    )
    assert obligation["guidance_actions"] == [
        "discover_safe_successor",
        "create_runnable_todo",
        "successor_or_supersede",
    ]
    assert obligation["satisfying_semantic_outcomes"] == [
        "fresh_vision_path_outcome",
        "new_runnable_successor",
        "new_concrete_blocker",
        "coverage_backed_exploration_exhausted",
        "coverage_backed_no_followup",
    ]
    assert "maintenance-only continuation does not satisfy" in obligation[
        "recommended_action"
    ]
    assert "watch-lane continuation" not in obligation["todo_actions"][-1]["text"]
    assert guard["replan_action_packet"]["uncovered_frontier"][
        "required_any_of"
    ] == obligation["satisfying_semantic_outcomes"]
    cli_actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    refresh_action = next(action for action in cli_actions if "refresh-state" in action)
    assert "--agent-vision-json" in refresh_action
    assert "--progress-result-class" not in refresh_action
    assert "--autonomous-replan-recorded" not in refresh_action
    assert "--repair-delta-kind" not in refresh_action


def test_agent_status_mirrors_quota_guidance_without_changing_peer() -> None:
    runs = [*_blocked_wait_polls(), _vision_run()]
    payload, raw_obligation = _status_payload_with_replan_runs(runs)
    assert raw_obligation is not None
    assert "new evidence-backed blocker or coverage-backed terminal" in raw_obligation[
        "recommended_action"
    ]

    item, peer_obligation = _set_two_agent_replan_obligations(
        payload,
        raw_obligation,
    )

    expected = _quota(payload)["autonomous_replan_obligation"]
    attach_agent_lane_next_actions(payload, agent_id=AGENT_ID)

    assert item["autonomous_replan_obligation"] == expected
    assert item["project_asset"]["autonomous_replan_obligation"] == expected
    assert (
        select_autonomous_replan_obligation(
            item,
            item["project_asset"],
            agent_id=AGENT_ID,
        )
        == expected
    )
    assert _quota(payload)["autonomous_replan_obligation"] == expected
    for target in (item, item["project_asset"]):
        obligations_by_agent = target["autonomous_replan_obligations_by_agent"]
        assert obligations_by_agent[AGENT_ID] == expected
        assert obligations_by_agent[PRIMARY_AGENT] == peer_obligation
    assert "maintenance-only continuation does not satisfy" in expected[
        "recommended_action"
    ]
    assert "new evidence-backed blocker or coverage-backed terminal" not in expected[
        "recommended_action"
    ]


def test_agent_status_preserves_as_needed_wait_guidance_from_quota() -> None:
    runs = [
        *_blocked_wait_polls(),
        _vision_run(advancement_policy="as_needed"),
    ]
    payload, _ = _status_payload_with_replan_runs(runs)

    expected = _quota(payload)["autonomous_replan_obligation"]
    attach_agent_lane_next_actions(payload, agent_id=AGENT_ID)

    item = payload["attention_queue"]["items"][0]
    assert item["autonomous_replan_obligation"] == expected
    assert item["project_asset"]["autonomous_replan_obligation"] == expected
    assert "new evidence-backed blocker or coverage-backed terminal" in expected[
        "recommended_action"
    ]
    assert "maintenance-only continuation does not satisfy" not in expected[
        "recommended_action"
    ]


def test_agent_status_keeps_vision_duty_after_legacy_actionable_ack() -> None:
    polls = _blocked_wait_polls()
    frontier_identity = polls[0]["monitor_target"]["frontier_identity"]
    ack = {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:03:00+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "frontier_identity": frontier_identity,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["runnable_todo_set"],
            },
        },
    }
    runs = [*polls, ack, _vision_run()]
    payload, raw_obligation = _status_payload_with_replan_runs(runs)
    assert raw_obligation is not None
    item, peer_obligation = _set_two_agent_replan_obligations(
        payload,
        raw_obligation,
    )
    expected = _quota(payload).get("autonomous_replan_obligation")
    assert expected is not None

    attach_agent_lane_next_actions(payload, agent_id=AGENT_ID)

    assert item["autonomous_replan_obligation"] == expected
    assert item["project_asset"]["autonomous_replan_obligation"] == expected
    for target in (item, item["project_asset"]):
        assert target["autonomous_replan_obligations_by_agent"] == {
            AGENT_ID: expected,
            PRIMARY_AGENT: peer_obligation,
        }


def test_as_needed_blocked_successor_guidance_requires_semantic_outcome() -> None:
    guard = _quota_with_replan_runs(
        [*_blocked_wait_polls(), _vision_run(advancement_policy="as_needed")]
    )

    obligation = guard["autonomous_replan_obligation"]
    assert obligation["guidance_actions"] == [
        "discover_safe_successor",
        "create_successor",
        "write_blocker",
        "record_coverage_terminal",
    ]
    assert "satisfying_repair_delta_kinds" not in obligation
    assert "new evidence-backed blocker or coverage-backed terminal" in obligation[
        "recommended_action"
    ]


def test_future_due_blocking_monitor_suppresses_predue_wait_replan() -> None:
    compacted_polls = [compact_run(poll) for poll in _blocked_wait_polls()]
    assert "next_due_at" not in compacted_polls[0]["monitor_target"]

    guard = _quota_with_replan_runs(
        [*compacted_polls, _vision_run()],
        extra_agent_items=_monitor_blocked_advancement_items(
            next_due_at="2099-01-01T00:00:00+00:00",
        ),
    )

    assert guard["decision"] == "skip"
    assert guard["effective_action"] == "monitor_quiet_skip"
    assert guard.get("autonomous_replan_obligation") is None
    assert guard["vision_wait_state"]["selected_todo_id"] == WAITING_ID


@pytest.mark.parametrize(
    "next_due_at",
    [None, "2026-07-16T00:00:00+00:00"],
    ids=["schedule-gap", "overdue"],
)
def test_unscheduled_or_overdue_monitor_preserves_wait_replan(
    next_due_at: str | None,
) -> None:
    compacted_polls = [compact_run(poll) for poll in _blocked_wait_polls()]

    guard = _quota_with_replan_runs(
        [*compacted_polls, _vision_run()],
        extra_agent_items=_monitor_blocked_advancement_items(
            next_due_at=next_due_at,
        ),
    )

    assert guard["decision"] == "autonomous_replan_required"
    trigger = guard["autonomous_replan_obligation"]["triggers"][0]
    assert trigger["kind"] == "blocked_successor_no_progress_repeat"


def test_peer_future_due_monitor_does_not_suppress_wait_replan() -> None:
    guard = _quota_with_replan_runs(
        [*[compact_run(poll) for poll in _blocked_wait_polls()], _vision_run()],
        extra_agent_items=_monitor_blocked_advancement_items(
            next_due_at="2099-01-01T00:00:00+00:00",
            claimed_by="peer-agent",
        ),
    )

    assert guard["decision"] == "autonomous_replan_required"
    trigger = guard["autonomous_replan_obligation"]["triggers"][0]
    assert trigger["kind"] == "blocked_successor_no_progress_repeat"


def test_interleaved_peer_monitor_does_not_reset_blocked_successor_replan() -> None:
    polls = _blocked_wait_polls()
    peer_poll = deepcopy(polls[0])
    peer_poll["agent_id"] = PRIMARY_AGENT
    peer_identity = {
        "agent_id": PRIMARY_AGENT,
        "target_id": "peer-monitor-target",
        "frontier_identity": "peer-frontier",
    }
    peer_poll["monitor_target"].update(peer_identity)
    peer_poll["monitor_event"]["agent_id"] = PRIMARY_AGENT
    peer_poll["monitor_event"]["monitor_target"].update(peer_identity)

    replanned = _quota_with_replan_runs(
        [polls[0], peer_poll, polls[1], _vision_run()]
    )

    assert replanned["decision"] == "autonomous_replan_required"
    obligation = replanned["autonomous_replan_obligation"]
    assert obligation["agent_id"] == AGENT_ID
    trigger = obligation["triggers"][0]
    assert trigger["kind"] == "blocked_successor_no_progress_repeat"
    assert trigger["monitor_target_id"] == polls[0]["monitor_target"]["target_id"]
    assert trigger["frontier_identity"] == polls[0]["monitor_target"][
        "frontier_identity"
    ]


def test_compacted_monitor_quiet_vision_waits_trigger_bounded_replan() -> None:
    guard = _quota(_status_payload())
    guard.update(
        {
            "decision": "skip",
            "effective_action": "monitor_quiet_skip",
            "should_run": False,
            "normal_delivery_allowed": False,
        }
    )
    polls = [
        build_quota_monitor_poll_event(
            guard,
            generated_at="2026-07-16T00:02:00+00:00",
        ),
        build_quota_monitor_poll_event(
            guard,
            generated_at="2026-07-16T00:01:00+00:00",
        ),
    ]

    assert {
        poll["monitor_target"]["monitor_mode"]
        for poll in polls
    } == {"blocked_successor_wait_without_material_transition"}
    compacted_polls = [compact_run(poll) for poll in polls]
    compact_target = compacted_polls[0]["monitor_target"]
    assert compact_target["target_id"] == polls[0]["monitor_target"]["target_id"]
    assert compact_target["frontier_identity"] == polls[0]["monitor_target"][
        "frontier_identity"
    ]
    assert "monitor_event" not in compacted_polls[0]

    replanned = _quota_with_replan_runs([*compacted_polls, _vision_run()])

    assert replanned["decision"] == "autonomous_replan_required"
    assert replanned["should_run"] is True
    trigger = replanned["autonomous_replan_obligation"]["triggers"][0]
    assert trigger["kind"] == "blocked_successor_no_progress_repeat"
    assert trigger["frontier_identity"] == compact_target["frontier_identity"]


def test_watch_ack_cannot_cover_newer_same_target_dead_monitor_repeat() -> None:
    guard = _generic_watch_quota(
        ack=_generic_watch_ack(frontier_identity="generic-watch-target")
    )

    assert guard["effective_action"] == "autonomous_replan_required"
    assert guard["autonomous_replan_obligation"]["required"] is True
    assert guard["goal_frontier_projection"]["replan_required"] is True


@pytest.mark.parametrize(
    ("ack", "advancement_policy", "expires_at"),
    [
        (None, "as_needed", "2099-02-01T00:00:00+00:00"),
        (
            _generic_watch_ack(frontier_identity="different-watch-target"),
            "as_needed",
            "2099-02-01T00:00:00+00:00",
        ),
        (
            _generic_watch_ack(frontier_identity="generic-watch-target"),
            "repeat_until_closed",
            "2099-02-01T00:00:00+00:00",
        ),
        (
            _generic_watch_ack(frontier_identity="generic-watch-target"),
            "as_needed",
            "2026-07-15T00:00:00+00:00",
        ),
    ],
    ids=["missing-ack", "target-drift", "repeat-vision", "expired-watch"],
)
def test_dead_monitor_repeat_rejects_noncausal_watch_ack(
    ack: dict | None,
    advancement_policy: str,
    expires_at: str,
) -> None:
    guard = _generic_watch_quota(
        ack=ack,
        advancement_policy=advancement_policy,
        expires_at=expires_at,
    )

    assert guard["decision"] == "autonomous_replan_required"
    obligation = guard["autonomous_replan_obligation"]
    assert obligation["frontier_identity"] == "generic-watch-target"
    assert obligation["triggers"][0]["kind"] == "dead_monitor_repeat"
    assert obligation["triggers"][0]["threshold"] == 6


def test_dead_monitor_repeat_rejects_unrelated_bounded_watch_evidence() -> None:
    unrelated_monitor_id = "todo_unrelated_bounded_monitor"
    unrelated_monitor = quota_todo_item(
        todo_id=unrelated_monitor_id,
        index=2,
        text="[P2] Monitor a different bounded frontier.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="unrelated-bounded-watch",
        cadence="7d",
        next_due_at="2099-01-01T00:00:00+00:00",
        expires_at="2099-02-01T00:00:00+00:00",
    )
    ack = _generic_watch_ack(frontier_identity="generic-watch-target")
    ack["autonomous_replan_ack"]["delta_contract"]["auto_evidence"][0][
        "todo_ids"
    ] = [unrelated_monitor_id]

    guard = _generic_watch_quota(
        ack=ack,
        extra_agent_items=[unrelated_monitor],
    )

    assert guard["decision"] == "autonomous_replan_required"
    assert guard["autonomous_replan_obligation"]["frontier_identity"] == (
        "generic-watch-target"
    )


def test_wait_only_ack_does_not_clear_repeat_vision_blocked_successor() -> None:
    polls = _blocked_wait_polls()
    frontier_identity = polls[0]["monitor_target"]["frontier_identity"]
    ack = {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:03:00+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "frontier_identity": frontier_identity,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["watch_lane_continuation"],
            },
        },
    }

    same_frontier = _quota_with_replan_runs([*polls, ack, _vision_run()])
    assert same_frontier["decision"] == "autonomous_replan_required"
    obligation = same_frontier["autonomous_replan_obligation"]
    assert obligation["frontier_identity"] == frontier_identity
    assert obligation["triggers"][0]["kind"] == (
        "blocked_successor_no_progress_repeat"
    )


def test_legacy_runnable_ack_cannot_close_blocked_successor_vision_duty() -> None:
    polls = _blocked_wait_polls()
    frontier_identity = polls[0]["monitor_target"]["frontier_identity"]
    ack = {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:03:00+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "frontier_identity": frontier_identity,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["runnable_todo_set"],
            },
        },
    }

    same_frontier = _quota_with_replan_runs([*polls, ack, _vision_run()])
    assert same_frontier["decision"] == "autonomous_replan_required"
    assert same_frontier["autonomous_replan_obligation"][
        "frontier_identity"
    ] == frontier_identity

    ack["autonomous_replan_ack"]["frontier_identity"] = "different-frontier"
    changed_frontier = _quota_with_replan_runs([*polls, ack, _vision_run()])
    assert changed_frontier["decision"] == "autonomous_replan_required"
    assert changed_frontier["autonomous_replan_obligation"][
        "frontier_identity"
    ] == frontier_identity


def test_wait_only_ack_cannot_close_blocked_successor_replan() -> None:
    polls = _blocked_wait_polls()
    frontier_identity = polls[0]["monitor_target"]["frontier_identity"]
    ack = {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:03:00+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "frontier_identity": frontier_identity,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["watch_lane_continuation"],
            },
        },
    }

    covered = _quota_with_replan_runs(
        [
            *polls,
            ack,
            _vision_run(advancement_policy="as_needed"),
        ]
    )

    assert covered["decision"] == "autonomous_replan_required"
    assert covered["autonomous_replan_obligation"]["required"] is True


def test_wait_ack_cannot_close_newer_same_frontier_stalls() -> None:
    polls = _blocked_wait_polls()
    frontier_identity = polls[0]["monitor_target"]["frontier_identity"]
    older_ack = {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-07-16T00:00:30+00:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "frontier_identity": frontier_identity,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["watch_lane_continuation"],
            },
        },
    }

    covered = _quota_with_replan_runs(
        [*polls, older_ack, _vision_run(advancement_policy="as_needed")]
    )

    assert covered["decision"] == "autonomous_replan_required"
    assert covered["goal_frontier_projection"]["replan_required"] is True
    assert covered["autonomous_replan_obligation"]["required"] is True

    older_ack["autonomous_replan_ack"]["frontier_identity"] = "changed-frontier"
    changed = _quota_with_replan_runs(
        [*polls, older_ack, _vision_run(advancement_policy="as_needed")]
    )
    assert changed["decision"] == "autonomous_replan_required"


def test_refresh_ack_preserves_the_observed_blocked_successor_identity(
    tmp_path,
) -> None:
    polls = _blocked_wait_polls()
    frontier_identity = latest_blocked_successor_frontier_identity(polls)
    assert frontier_identity == polls[0]["monitor_target"]["frontier_identity"]

    record = build_state_refresh_record(
        goal_id=GOAL_ID,
        state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
        state_text="---\nstatus: active\n---\n\n## Next Action\n\n- Replan.\n",
        classification="autonomous_replan_recorded",
        recommended_action="Continue the selected bounded replan slice.",
        recommended_action_source="explicit_arg",
        generated_at="2026-07-16T00:03:00+00:00",
        registry_goal=None,
        agent_id=AGENT_ID,
        autonomous_replan_recorded=True,
        repair_delta_contract={
            "schema_version": "repair_delta_contract_v0",
            "delta_present": True,
            "delta_kinds": ["watch_lane_continuation"],
        },
        autonomous_replan_frontier_identity=frontier_identity,
    )

    assert record["autonomous_replan_ack"]["frontier_identity"] == (
        frontier_identity
    )


def test_refresh_ack_recovers_generic_dead_monitor_target_identity() -> None:
    polls = _generic_quiet_polls(target_id="bounded-generic-target")

    assert latest_monitor_replan_frontier_identity(
        polls,
        agent_id=AGENT_ID,
    ) == "bounded-generic-target"

    intermediary = {
        "classification": "todo_completion",
        "generated_at": "2026-07-16T00:02:30+00:00",
        "agent_id": AGENT_ID,
    }
    assert (
        latest_monitor_replan_frontier_identity(
            [intermediary, *polls],
            agent_id=AGENT_ID,
        )
        is None
    )
    assert latest_monitor_replan_frontier_identity(
        [intermediary, *polls],
        agent_id=AGENT_ID,
        watch_todo_ids=[MONITOR_ID],
    ) == "bounded-generic-target"
    assert (
        latest_monitor_replan_frontier_identity(
            [intermediary, *polls],
            agent_id=AGENT_ID,
            watch_todo_ids=[MONITOR_ID, "todo_second_watch"],
        )
        is None
    )
    blocked_poll = deepcopy(polls[0])
    blocked_poll["monitor_target"] = {
        **blocked_poll["monitor_target"],
        "monitor_mode": "blocked_successor_wait_without_material_transition",
        "frontier_identity": "blocked-successor-frontier",
    }
    assert (
        latest_monitor_replan_frontier_identity(
            [intermediary, blocked_poll],
            agent_id=AGENT_ID,
            watch_todo_ids=[MONITOR_ID],
        )
        is None
    )

    conflicting = _generic_quiet_polls(target_id="conflicting-generic-target")
    conflicting[0]["monitor_target"]["agent_id"] = PRIMARY_AGENT
    assert latest_monitor_replan_frontier_identity(
        conflicting,
        agent_id=AGENT_ID,
    ) is None

    polls[0]["agent_id"] = PRIMARY_AGENT
    polls[0]["monitor_target"]["agent_id"] = PRIMARY_AGENT
    assert latest_monitor_replan_frontier_identity(
        polls,
        agent_id=AGENT_ID,
    ) == "bounded-generic-target"


def test_legacy_watch_ack_cannot_close_current_agent_frontier(tmp_path) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    state_path = project / ".codex" / "goals" / "half-speed" / "ACTIVE_GOAL_STATE.md"
    _append_bounded_watch_todo(state_path, claimed_by=SCOPED_AGENT_ID)
    peer_agent_id = "codex-main-control"
    current_frontier = "current-agent-frontier"
    peer_frontier = "newer-peer-frontier"

    def monitor_poll(*, agent_id: str, frontier_identity: str, generated_at: str) -> dict:
        return {
            "goal_id": "half-speed",
            "classification": "quota_monitor_poll",
            "generated_at": generated_at,
            "agent_id": agent_id,
            "monitor_target": {
                "target_id": frontier_identity,
                "monitor_mode": "blocked_successor_wait_without_material_transition",
                "agent_id": agent_id,
                "frontier_identity": frontier_identity,
            },
        }

    runs = [
        monitor_poll(
            agent_id=peer_agent_id,
            frontier_identity=peer_frontier,
            generated_at="2099-01-01T00:03:00+00:00",
        ),
        monitor_poll(
            agent_id=SCOPED_AGENT_ID,
            frontier_identity=current_frontier,
            generated_at="2099-01-01T00:02:00+00:00",
        ),
    ]
    assert latest_blocked_successor_frontier_identity(runs) == peer_frontier
    assert latest_blocked_successor_frontier_identity(
        runs,
        agent_id=SCOPED_AGENT_ID,
    ) == current_frontier

    unscoped_poll = monitor_poll(
        agent_id=SCOPED_AGENT_ID,
        frontier_identity="goal-level-frontier",
        generated_at="2099-01-01T00:04:00+00:00",
    )
    unscoped_poll.pop("agent_id")
    unscoped_poll["monitor_target"].pop("agent_id")
    assert latest_blocked_successor_frontier_identity([unscoped_poll]) == (
        "goal-level-frontier"
    )
    assert (
        latest_blocked_successor_frontier_identity(
            [unscoped_poll],
            agent_id=SCOPED_AGENT_ID,
        )
        is None
    )

    conflicting_poll = monitor_poll(
        agent_id=SCOPED_AGENT_ID,
        frontier_identity="conflicting-frontier",
        generated_at="2099-01-01T00:05:00+00:00",
    )
    conflicting_poll["monitor_target"]["agent_id"] = peer_agent_id
    assert (
        latest_blocked_successor_frontier_identity(
            [conflicting_poll],
            agent_id=SCOPED_AGENT_ID,
        )
        is None
    )

    index_path = runtime_root / "goals" / "half-speed" / "runs" / "index.jsonl"
    with index_path.open("a", encoding="utf-8") as index_file:
        executed_runs = _executed_monitor_polls(
            target_id=current_frontier,
            agent_id=SCOPED_AGENT_ID,
            date="2099-01-01",
        )
        for run in reversed(executed_runs):
            index_file.write(json.dumps(run, ensure_ascii=False) + "\n")

    with pytest.raises(ValueError, match="typed semantic delta"):
        refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id="half-speed",
            project=None,
            state_file=None,
            classification="autonomous_replan_recorded",
            recommended_action="Continue the current agent replan.",
            agent_id=SCOPED_AGENT_ID,
            autonomous_replan_recorded=True,
            repair_delta_kinds=["watch_lane_continuation"],
            dry_run=True,
            sync_global=False,
        )


def test_legacy_watch_ack_cannot_cross_material_run_with_todo_evidence(
    tmp_path,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    state_path = project / ".codex" / "goals" / "half-speed" / "ACTIVE_GOAL_STATE.md"
    _append_bounded_watch_todo(state_path, claimed_by=SCOPED_AGENT_ID)
    target_id = "postdelivery-watch-target"
    poll = {
        "goal_id": "half-speed",
        "classification": "quota_monitor_poll",
        "generated_at": "2099-01-01T00:02:00+00:00",
        "agent_id": SCOPED_AGENT_ID,
        "todo_id": MONITOR_ID,
        "target_key": "bounded-watch",
        "monitor_target": {
            "monitor_mode": "monitor_quiet_until_material_transition",
            "agent_id": SCOPED_AGENT_ID,
            "target_id": target_id,
        },
    }
    material_run = {
        "goal_id": "half-speed",
        "classification": "todo_completion",
        "generated_at": "2099-01-01T00:03:00+00:00",
        "agent_id": SCOPED_AGENT_ID,
    }
    index_path = runtime_root / "goals" / "half-speed" / "runs" / "index.jsonl"
    with index_path.open("a", encoding="utf-8") as index_file:
        for run in (poll, material_run):
            index_file.write(json.dumps(run, ensure_ascii=False) + "\n")
        for run in reversed(
            _executed_monitor_polls(
                target_id=target_id,
                agent_id=SCOPED_AGENT_ID,
                date="2099-01-01",
            )
        ):
            index_file.write(json.dumps(run, ensure_ascii=False) + "\n")

    with pytest.raises(ValueError, match="typed semantic delta"):
        refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id="half-speed",
            project=None,
            state_file=None,
            classification="postdelivery_watch_replan",
            recommended_action="Keep the exact bounded watch active.",
            agent_id=SCOPED_AGENT_ID,
            autonomous_replan_recorded=True,
            repair_delta_kinds=["watch_lane_continuation"],
            dry_run=True,
            sync_global=False,
        )


def test_status_projects_exact_blocker_and_resume_contract() -> None:
    payload = _status_payload(waiting_status="deferred")
    attach_agent_lane_next_actions(payload, agent_id=AGENT_ID)

    item = payload["attention_queue"]["items"][0]
    wait = item["goal_frontier_projection"]["vision_wait_state"]
    assert wait["selected_todo_id"] == WAITING_ID
    assert item["project_asset"]["goal_frontier_projection"]["vision_wait_state"] == wait
    markdown = render_status_markdown(payload)
    assert (
        "vision_wait_state: state=waiting "
        f"todo_id={WAITING_ID} resume_when=todo_done:{BLOCKER_ID} "
        "automatic_resume=True"
    ) in markdown


def test_cleared_blocker_restores_normal_open_successor_routing() -> None:
    guard = _quota(_status_payload(blocker_status="done"))

    assert guard["decision"] == "run"
    assert guard["normal_delivery_allowed"] is True
    assert guard["selected_todo"]["todo_id"] == WAITING_ID
    assert guard["agent_lane_next_action"]["resume_ready"] is True
    assert "vision_wait_state" not in guard
    assert "vision_wait_state" not in guard["goal_frontier_projection"]
    assert guard["goal_frontier_projection"]["acceptance_gaps"][0]["kind"] == (
        "vision_acceptance_gap"
    )
    assert guard["vision_continuation_audit"]["required"] is True


def test_missing_checkpoint_is_not_hidden_by_blocked_successor() -> None:
    guard = _quota(_status_payload(missing_checkpoint=True))

    assert guard["decision"] == "autonomous_replan_required"
    assert "vision_wait_state" not in guard
    gap_kinds = {
        gap["kind"] for gap in guard["goal_frontier_projection"]["acceptance_gaps"]
    }
    assert "vision_checkpoint_missing" in gap_kinds
    assert guard["vision_continuation_audit"]["required"] is True


def test_closed_stage_successor_gap_is_not_hidden_by_blocked_successor() -> None:
    payload = _status_payload(vision_state="vision_closed")
    item = payload["attention_queue"]["items"][0]
    context = build_goal_frontier_projection_context_from_status(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        status_payload=payload,
        item=item,
        project_asset=item["project_asset"],
        user_todo_summary=item["user_todos"],
        agent_todo_summary=item["agent_todos"],
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        neutral_replan_ack_classifications=set(),
        registered_agent_ids=[PRIMARY_AGENT, AGENT_ID],
        goal_status="active",
    )

    frontier = context["goal_frontier_projection"]
    assert "vision_wait_state" not in frontier
    assert frontier["replan_required"] is True
    assert {
        gap["kind"] for gap in frontier["acceptance_gaps"]
    } == {"vision_successor_required"}


def test_standing_monitor_prerequisite_keeps_dedicated_repair_route() -> None:
    guard = _quota(
        _status_payload(
            blocker_task_class="continuous_monitor",
            vision_state="retired",
        )
    )

    assert guard["decision"] == "run"
    assert "vision_wait_state" not in guard
    assert guard["work_lane_contract"]["obligation"] == (
        "repair_resume_gate_or_close_standing_monitor"
    )
    assert "resume_blocked_by_open_monitor" in guard["work_lane_contract"][
        "reason_codes"
    ]
    assert guard["selected_todo"]["todo_id"] == WAITING_ID
