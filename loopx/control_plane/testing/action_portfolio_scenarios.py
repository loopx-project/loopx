from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..quota.cli_projection import compact_quota_should_run_cli_payload
from ..quota.effective_action import EffectiveAction
from ..quota.should_run import build_quota_should_run
from ..work_items.interaction_contract import build_interaction_contract
from ..quota.turn_envelope import quota_action_signature_document
from .quota_fixtures import quota_status_payload, quota_todo_item


ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID = "portfolio-goal"
ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID = "codex-portfolio"


def accepted_replan_successor_status() -> dict[str, Any]:
    """An accepted new route competes with an older, higher-priority default."""
    agent = ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID
    obligation = "replan-0123456789abcdef"
    old = quota_todo_item(
        todo_id="todo_prior_plan",
        index=1,
        priority="P0",
        title="Validate the original route.",
        claimed_by=agent,
    )
    successor = quota_todo_item(
        todo_id="todo_replan_successor",
        index=2,
        priority="P1",
        title="Validate the revised route against the new evidence.",
        claimed_by=agent,
        action_kind="validate",
        target_key="revised-route",
        replan_obligation_id=obligation,
    )
    return quota_status_payload(
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        status="active",
        agent_todo_items=[old, successor],
        recommended_action=old["text"],
        next_action=old["text"],
        active_state_next_action=old["text"],
        coordination={"agent_model": "peer_v1", "registered_agents": [agent]},
        claim_scope_agent_id=agent,
        latest_runs=[
            {
                "agent_id": agent,
                "classification": "bounded_replan_progress",
                "generated_at": "2026-08-01T01:00:00Z",
                "autonomous_replan_ack": {
                    "schema_version": "autonomous_replan_ack_v0",
                    "recorded": True,
                    "source": "todo_replan_successor_transition",
                    "semantic_delta": {
                        "schema_version": "replan_semantic_delta_v0",
                        "accepted": True,
                        "outcomes": ["new_runnable_successor"],
                        "satisfying_outcomes": ["new_runnable_successor"],
                        "obligation_id": obligation,
                        "successor_origin_obligation_id": obligation,
                        "successor_todo_id": successor["todo_id"],
                    },
                },
            }
        ],
    )


def accepted_replan_successor_scenario_source() -> dict[str, Any]:
    return build_quota_should_run(
        accepted_replan_successor_status(),
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )


PLANNING_HORIZON_STRATEGIC_CONTEXT_TODO_IDS = (
    "todo_regression_gate",
    "todo_per_model_tests",
    "todo_runtime_admission",
    "todo_allowlist_policy",
    "todo_facts_source",
)
PLANNING_HORIZON_STRATEGIC_CONTEXT_ATTENTION_IDS = (
    "todo_per_model_tests",
    "todo_runtime_admission",
    "todo_allowlist_policy",
)
PLANNING_HORIZON_STRATEGIC_CONTEXT_SUCCESSOR_RELATIONS = frozenset(
    {
        ("todo_per_model_tests", "todo_regression_gate"),
        ("todo_runtime_admission", "todo_per_model_tests"),
        ("todo_allowlist_policy", "todo_runtime_admission"),
        ("todo_facts_source", "todo_allowlist_policy"),
    }
)


def _planning_horizon_chain_todo(
    *,
    todo_id: str,
    index: int,
    title: str,
    successor_todo_id: str | None,
    resume_when: str | None,
    priority: str = "P0",
) -> dict[str, Any]:
    return quota_todo_item(
        todo_id=todo_id,
        index=index,
        priority=priority,
        title=title,
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
        status="deferred",
        resume_when=resume_when,
        successor_todo_ids=[successor_todo_id] if successor_todo_id else [],
    )


def planning_horizon_strategic_context_status() -> dict[str, Any]:
    """Build typed source facts without consulting the horizon reducer output."""

    facts = _planning_horizon_chain_todo(
        todo_id="todo_facts_source",
        index=1,
        title="Establish the authoritative model facts source.",
        successor_todo_id="todo_allowlist_policy",
        resume_when="pr_merged:#fixture-source",
    )
    allowlist = _planning_horizon_chain_todo(
        todo_id="todo_allowlist_policy",
        index=2,
        title="Define the model allowlist and modality policy.",
        successor_todo_id="todo_runtime_admission",
        resume_when="todo_done:todo_facts_source",
    )
    runtime = _planning_horizon_chain_todo(
        todo_id="todo_runtime_admission",
        index=3,
        title="Apply the allowlist at runtime admission.",
        successor_todo_id="todo_per_model_tests",
        resume_when="todo_done:todo_allowlist_policy",
    )
    model_tests = _planning_horizon_chain_todo(
        todo_id="todo_per_model_tests",
        index=4,
        title="Prove each admitted model and modality.",
        successor_todo_id="todo_regression_gate",
        resume_when="todo_done:todo_runtime_admission",
        priority="P1",
    )
    selected = quota_todo_item(
        todo_id="todo_regression_gate",
        index=5,
        priority="P1",
        title="Run the local regression gate.",
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )
    return quota_status_payload(
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        status="active",
        agent_todo_items=[facts, allowlist, runtime, model_tests, selected],
        recommended_action=selected["text"],
        next_action=selected["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID],
        },
        claim_scope_agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )


def planning_horizon_strategic_context_scenario_source() -> dict[str, Any]:
    return build_quota_should_run(
        planning_horizon_strategic_context_status(),
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )


def validate_planning_horizon_strategic_context_scenario(
    source_packet: Mapping[str, Any],
) -> None:
    """Validate fixed typed facts before compact projection or provider spend."""

    horizon = source_packet.get("planning_horizon")
    if not isinstance(horizon, Mapping):
        raise ValueError("planning-horizon scenario is missing the production horizon")
    work_items = list(horizon.get("work_items") or [])
    visible_ids = tuple(
        str(item.get("todo_id") or "")
        for item in work_items
        if isinstance(item, Mapping)
    )
    relations = list(horizon.get("relations") or [])
    successor_relations = frozenset(
        (
            str(item.get("from_todo_id") or ""),
            str(item.get("to_ref") or ""),
        )
        for item in relations
        if isinstance(item, Mapping) and item.get("relation") == "successor"
    )
    relation_kinds = {
        str(item.get("relation") or "")
        for item in relations
        if isinstance(item, Mapping)
    }
    completeness = horizon.get("completeness")
    selection = horizon.get("selection_contract")
    if not (
        horizon.get("schema_version") == "quota_planning_horizon_v0"
        and horizon.get("mode") == "read_only"
        and horizon.get("selected_todo_id") == "todo_regression_gate"
        and visible_ids == PLANNING_HORIZON_STRATEGIC_CONTEXT_TODO_IDS
        and tuple(horizon.get("attention_todo_ids") or [])
        == PLANNING_HORIZON_STRATEGIC_CONTEXT_ATTENTION_IDS
        and successor_relations
        == PLANNING_HORIZON_STRATEGIC_CONTEXT_SUCCESSOR_RELATIONS
        and relation_kinds == {"successor", "resumes_when"}
        and isinstance(completeness, Mapping)
        and completeness.get("source_context_todo_count") == 5
        and completeness.get("candidate_input_count") == 5
        and completeness.get("complete") is True
        and isinstance(selection, Mapping)
        and selection.get("horizon_changes_selection") is False
        and selection.get("explicit_selection_required_for_other_work") is True
    ):
        raise ValueError(
            "planning-horizon scenario must preserve the fixed typed strategic chain"
        )


def future_primary_fallback_scenario_source() -> dict[str, Any]:
    future_primary = quota_todo_item(
        todo_id="todo_future_primary",
        index=1,
        priority="P0",
        title="Poll the primary target at its next due window.",
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
        task_class="continuous_monitor",
        action_kind="monitor",
        target_key="public-fixture-primary",
        cadence="daily",
        next_due_at="2099-01-01T00:00:00Z",
        watch_only=True,
    )
    ready_fallback = quota_todo_item(
        todo_id="todo_ready_fallback",
        index=2,
        priority="P1",
        title="Advance the ready bounded fallback slice.",
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )
    status = quota_status_payload(
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        status="active",
        agent_todo_items=[future_primary, ready_fallback],
        recommended_action=future_primary["text"],
        next_action=future_primary["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID],
        },
        claim_scope_agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )
    return build_quota_should_run(
        status,
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )


def external_wait_fallback_scenario_source() -> dict[str, Any]:
    waiting_primary = quota_todo_item(
        todo_id="todo_external_wait_primary",
        index=1,
        priority="P0",
        title="Resume the validated primary slice after external state changes.",
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
        resume_when="monitor_changed:todo_external_wait_monitor",
        resume_monitor_generation=4,
        successor_todo_ids=["todo_external_wait_fallback"],
        note="Do not poll this Todo; its typed monitor condition is still pending.",
    )
    ready_fallback = quota_todo_item(
        todo_id="todo_external_wait_fallback",
        index=2,
        priority="P1",
        title="Advance the independent bounded fallback slice.",
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
        note="Implement the fallback and run its focused validation.",
    )
    monitor = quota_todo_item(
        todo_id="todo_external_wait_monitor",
        index=3,
        priority="P2",
        title="Observe the external lifecycle for a material change.",
        claimed_by=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
        task_class="continuous_monitor",
        action_kind="monitor",
        target_key="public-fixture-external-lifecycle",
        cadence="daily",
        next_due_at="2099-01-01T00:00:00Z",
        watch_only=True,
        material_change_generation=4,
    )
    status = quota_status_payload(
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        status="active",
        agent_todo_items=[waiting_primary, ready_fallback, monitor],
        recommended_action=waiting_primary["text"],
        next_action=waiting_primary["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID],
        },
        claim_scope_agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )
    return build_quota_should_run(
        status,
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )


def validate_future_primary_fallback_scenario(
    source_packet: Mapping[str, Any],
) -> None:
    signature = quota_action_signature_document(source_packet)
    action = dict(signature.get("action") or {})
    selected = dict(action.get("selected_todo") or {})
    portfolio = dict(action.get("action_portfolio") or {})
    primary = dict(portfolio.get("primary") or {})
    unavailable = list(portfolio.get("unavailable_higher_priority") or [])
    first_unavailable = dict(unavailable[0]) if unavailable else {}
    if not (
        selected.get("todo_id") == "todo_ready_fallback"
        and primary.get("todo_id") == "todo_ready_fallback"
        and first_unavailable.get("todo_id") == "todo_future_primary"
        and first_unavailable.get("availability_reason") == "scheduled_for_future"
    ):
        raise ValueError(
            "future-primary scenario must execute the ready fallback while "
            "preserving the unavailable higher-priority monitor"
        )


def validate_external_wait_fallback_scenario(
    source_packet: Mapping[str, Any],
) -> None:
    signature = quota_action_signature_document(source_packet)
    action = dict(signature.get("action") or {})
    selected = dict(action.get("selected_todo") or {})
    portfolio = dict(action.get("action_portfolio") or {})
    unavailable = list(portfolio.get("unavailable_higher_priority") or [])
    first_unavailable = dict(unavailable[0]) if unavailable else {}
    summary = dict(source_packet.get("agent_todo_summary") or {})
    resume_blocked = list(summary.get("resume_blocked_items") or [])
    first_resume_blocked = dict(resume_blocked[0]) if resume_blocked else {}
    condition = dict(first_resume_blocked.get("resume_condition") or {})
    compact = compact_quota_should_run_cli_payload(dict(source_packet))
    compact_portfolio = dict(compact.get("action_portfolio") or {})
    suggested = list(compact_portfolio.get("suggested_actions") or [])
    first_suggested = dict(suggested[0]) if suggested else {}
    if not (
        selected.get("todo_id") == "todo_external_wait_fallback"
        and source_packet.get("recommended_action")
        == "[P1] Advance the independent bounded fallback slice."
        and portfolio.get("schema_version") == "quota_action_portfolio_v2"
        and first_unavailable.get("todo_id") == "todo_external_wait_primary"
        and first_unavailable.get("availability_reason") == "resume_condition_pending"
        and first_resume_blocked.get("todo_id") == "todo_external_wait_primary"
        and first_resume_blocked.get("resume_ready") is False
        and condition.get("kind") == "monitor_changed"
        and condition.get("baseline_generation") == 4
        and condition.get("material_change_generation") == 4
        and first_suggested.get("todo_id") == "todo_external_wait_fallback"
        and first_suggested.get("continuation_hint")
        == "Implement the fallback and run its focused validation."
    ):
        raise ValueError(
            "external-wait scenario must expose the pending P0 condition while "
            "executing the bounded fallback from the compact default packet"
        )


def turn_scenario_source(
    *,
    human_gate: bool,
    agent_id: str = ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    continuation_policy: str | None = None,
) -> dict[str, Any]:
    selected_todo = None
    if not human_gate:
        selected_todo = {
            "todo_id": "todo_portfolio001",
            "status": "open",
            "task_class": "advancement_task",
            "claimed_by": agent_id,
            "text": "Implement one bounded public-safe slice.",
        }
        if continuation_policy:
            selected_todo["continuation_policy"] = continuation_policy
    payload: dict[str, Any] = {
        "ok": True,
        "mode": "should-run",
        "goal_id": ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        "decision": "skip" if human_gate else "run",
        "should_run": not human_gate,
        "effective_action": EffectiveAction.OPERATOR_GATE_NOTIFY.value if human_gate else EffectiveAction.NORMAL_RUN.value,
        "state": "operator_gate" if human_gate else "eligible",
        "requires_user_action": human_gate,
        "gate_prompt": ("Approve the bounded public release." if human_gate else None),
        "recommended_action": (
            "Approve the bounded public release."
            if human_gate
            else "Implement one bounded public-safe slice."
        ),
        "selected_todo": selected_todo,
        "agent_identity": {"agent_id": agent_id},
        "execution_obligation": {
            "must_attempt_work": not human_gate,
            "delivery_allowed": not human_gate,
        },
        "normal_delivery_allowed": not human_gate,
        "heartbeat_recommendation": {
            "notify": "NOTIFY" if human_gate else "DONT_NOTIFY"
        },
        "goal_boundary": {
            "write_scope": ["loopx/**", "tests/**"],
            "guards": ["stop before external writes"],
        },
    }
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=["network"],
    )
    payload["action_required"] = human_gate
    payload["open_count"] = 1 if human_gate else 0
    return payload
