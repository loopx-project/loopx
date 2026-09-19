from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.capability_hooks import (
    POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
    PostWritebackHookRegistration,
)
from ...control_plane.goals.goal_frontier import (
    build_goal_frontier_projection_from_summaries,
)
from ...control_plane.todos.active_state_todo_parser import parse_active_state_todos
from ...control_plane.todos.quota_summary import summarize_user_todos_for_quota
from ...control_plane.todos.todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
from ...history import collect_history, load_registry
from ...paths import resolve_runtime_root
from ...registry import registry_goals
from ...rollout_event_log import load_rollout_events, rollout_event_log_path
from .stage_completion import STAGE_COMPLETION_RECEIPT_SCHEMA
from .stage_completion import derive_periodic_report_stage_completion_from_runs
from .presets import build_periodic_report_preset_activation
from .project_progress_snapshot import build_project_progress_snapshot_from_state
from .incremental import read_periodic_report_goal_publication_cursors
from .machine_defaults import resolve_goal_periodic_report_subscription
from .machine_store import read_periodic_report_machine_defaults
from .triggers import build_periodic_report_trigger_decision
from .request_action import REQUEST_ACTION_SCHEMA


PERIODIC_REPORT_POST_WRITEBACK_HOOK_ID = "periodic_report.runtime_trigger"
PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT = "periodic_report.trigger_evaluation"


def _result(*, status: str, intent: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
        "hook_id": PERIODIC_REPORT_POST_WRITEBACK_HOOK_ID,
        "capability_id": "periodic-report",
        "phase": "post_writeback",
        "status": status,
        "intent": dict(intent) if intent is not None else None,
    }


def _goal_config(registry_path: Path, goal_id: str) -> Mapping[str, Any]:
    registry = load_registry(registry_path)
    goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "").strip() == str(goal_id or "").strip()
        ),
        None,
    )
    if not isinstance(goal, Mapping):
        return {}
    control_plane = goal.get("control_plane")
    if not isinstance(control_plane, Mapping):
        return {}
    value = control_plane.get("periodic_report")
    return value if isinstance(value, Mapping) else {}


def periodic_report_post_writeback_hooks_for_goal(
    *, registry_path: Path, goal_id: str, runtime_root: Path | None = None
) -> tuple[PostWritebackHookRegistration, ...]:
    """Resolve a Goal override or live machine-default profile at composition.

    Both branches resolve through the canonical subscription resolver, so an
    invalid Goal override is reported here the same way the delivery paths
    report it. Store read and subscription validation failures degrade to no
    hooks with one warning: composition happens outside the dispatch
    isolation boundary, so optional hooks never alter the primary truth of
    the CLI command that composes them.
    """

    registry = load_registry(registry_path)
    effective_runtime_root = runtime_root or resolve_runtime_root(
        registry,
        registry_path=registry_path,
    )
    goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "").strip() == str(goal_id or "").strip()
        ),
        None,
    )
    if not isinstance(goal, Mapping):
        return ()
    goal_override = _goal_config(registry_path, goal_id)
    try:
        subscription = resolve_goal_periodic_report_subscription(
            goal,
            (
                None
                if goal_override
                else read_periodic_report_machine_defaults(effective_runtime_root)
            ),
        )
    except (TypeError, ValueError) as exc:
        warnings.warn(
            f"periodic-report subscription for goal {goal_id} failed to resolve; "
            f"post-writeback hooks are disabled: {exc}",
            UserWarning,
            stacklevel=2,
        )
        return ()
    if subscription.get("enabled") is not True:
        return ()
    preset = str(subscription.get("profile_preset") or "").strip()
    if not preset:
        return ()
    activation = build_periodic_report_preset_activation(preset)
    if activation.get("active") is not True:
        return ()
    profile = activation.get("profile")
    if not isinstance(profile, Mapping):
        return ()
    return (
        periodic_report_post_writeback_hook(
            profile_ref={
                "profile_id": str(profile.get("profile_id") or ""),
                "profile_version": str(profile.get("profile_version") or ""),
                "profile_digest": str(activation.get("profile_digest") or ""),
            },
            trigger_policy=(
                dict(profile.get("trigger_policy"))
                if isinstance(profile.get("trigger_policy"), Mapping)
                else {}
            ),
            policy_version=(
                "weekly-" + str(activation.get("profile_digest") or "")[-16:]
            ),
        ),
    )


def _frontier_projection(
    *,
    state_text: str,
    goal: Mapping[str, Any],
    state_path: Path,
    goal_id: str,
    agent_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    todos = parse_active_state_todos(
        state_text,
        goal=dict(goal),
        state_path=state_path,
        item_limit=None,
    )
    raw_user_summary = (
        dict(todos.get("user_todos"))
        if isinstance(todos.get("user_todos"), Mapping)
        else {}
    )
    raw_agent_summary = (
        dict(todos.get("agent_todos"))
        if isinstance(todos.get("agent_todos"), Mapping)
        else {}
    )
    user_summary = summarize_user_todos_for_quota(raw_user_summary) or {}
    agent_summary = summarize_user_todos_for_quota(raw_agent_summary) or {}
    projection = build_goal_frontier_projection_from_summaries(
        goal_id=goal_id,
        agent_id=agent_id,
        user_todo_summary=user_summary,
        agent_todo_summary=agent_summary,
        work_lane_contract=None,
        replan_obligation=None,
    )
    projection["source"] = "periodic_report_post_writeback"
    normalized = projection.get("normalized_progress")
    projection["blocking_handoff_gate_count"] = (
        int(normalized.get("user_open_count") or 0)
        if isinstance(normalized, Mapping)
        else 0
    )
    return projection, user_summary, agent_summary


def build_periodic_report_post_writeback_projection(
    *,
    payload: Mapping[str, Any],
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, object]:
    """Reduce private runtime state to one bounded public-safe stage receipt."""

    normalized_agent_id = str(agent_id or "").strip()
    state = payload.get("state")
    state_path_value = (
        state.get("path") if isinstance(state, Mapping) else None
    ) or payload.get("state_file")
    state_path = Path(str(state_path_value or "")).expanduser()
    if not normalized_agent_id or not state_path.is_file():
        return {}
    registry = load_registry(registry_path)
    goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "").strip() == str(goal_id or "").strip()
        ),
        {},
    )
    state_text = state_path.read_text(encoding="utf-8")
    projection, _user_summary, _agent_summary = _frontier_projection(
        state_text=state_text,
        goal=goal,
        state_path=state_path,
        goal_id=goal_id,
        agent_id=normalized_agent_id,
    )
    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        limit=64,
        include_runtime_goals=False,
    )
    goals = history.get("goals")
    goal_history = goals[0] if isinstance(goals, list) and goals else {}
    latest_runs = (
        list(goal_history.get("latest_runs") or [])
        if isinstance(goal_history, Mapping)
        else []
    )
    settled_ack: Mapping[str, Any] | None = None
    for run in latest_runs:
        if not isinstance(run, Mapping):
            continue
        run_agent_id = str(run.get("agent_id") or "").strip()
        vision = run.get("agent_vision")
        vision_agent_id = (
            str(vision.get("agent_id") or "").strip()
            if isinstance(vision, Mapping)
            else ""
        )
        if run_agent_id and vision_agent_id and run_agent_id != vision_agent_id:
            continue
        attributed_agent_id = run_agent_id or vision_agent_id
        if attributed_agent_id != normalized_agent_id:
            continue
        raw_ack = run.get("autonomous_replan_ack")
        ack = dict(raw_ack) if isinstance(raw_ack, Mapping) else None
        if ack is not None:
            ack["agent_id"] = attributed_agent_id
        semantic_delta = ack.get("semantic_delta") if isinstance(ack, Mapping) else None
        if (
            isinstance(ack, Mapping)
            and ack.get("recorded") is True
            and isinstance(semantic_delta, Mapping)
            and "vision_successor_required"
            in {str(value) for value in semantic_delta.get("trigger_kinds") or []}
        ):
            settled_ack = ack
            break
    settled_obligation = None
    if settled_ack is not None:
        settled_obligation = {
            "frontier_identity": settled_ack.get("frontier_identity"),
            "agent_id": normalized_agent_id,
            "triggers": [{"kind": "vision_successor_required"}],
        }
    receipt = derive_periodic_report_stage_completion_from_runs(
        latest_runs=latest_runs,
        agent_id=normalized_agent_id,
        goal_frontier_projection=projection,
        settled_replan_obligation=settled_obligation,
        settled_replan_ack=settled_ack,
    )
    if receipt is None:
        return {}
    result: dict[str, object] = {"stage_completion": receipt}
    goal_cursors = read_periodic_report_goal_publication_cursors(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    publication_cursor = next(
        (c for c in goal_cursors if c["agent_id"] == normalized_agent_id), None
    )
    available_capabilities = payload.get("available_capabilities")
    if available_capabilities is None and isinstance(payload.get("turn"), Mapping):
        available_capabilities = payload["turn"].get("available_capabilities")
    project_progress = build_project_progress_snapshot_from_state(
        state_text=state_text,
        goal=goal,
        state_path=state_path,
        goal_id=goal_id,
        agent_id=normalized_agent_id,
        completed_at=str(receipt["completed_at"]),
        publication_cursor=publication_cursor,
        goal_cursors=goal_cursors,
        available_capabilities=available_capabilities,
        rollout_events=load_rollout_events(
            rollout_event_log_path(runtime_root, goal_id),
            limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
        ),
    )
    if publication_cursor is not None and project_progress is None:
        return {}
    if publication_cursor is not None:
        result["last_report"] = {
            "delivered_at": publication_cursor["delivered_at"],
            "covered_trigger_ids": publication_cursor["covered_trigger_ids"],
        }
    if project_progress is not None:
        result["project_progress"] = project_progress
    if isinstance(available_capabilities, list) and available_capabilities:
        result["available_capabilities"] = list(available_capabilities)
    return result


def periodic_report_post_writeback_hook(
    *,
    profile_ref: Mapping[str, Any] | None = None,
    trigger_policy: Mapping[str, Any] | None = None,
    policy_version: str = "v0",
) -> PostWritebackHookRegistration:
    """Register stage reporting as an effect-free post-writeback intent producer."""

    def producer(hook_input: Mapping[str, Any]) -> dict[str, Any]:
        projection = hook_input.get("projection")
        stage = (
            projection.get("stage_completion")
            if isinstance(projection, Mapping)
            else None
        )
        if (
            not isinstance(stage, Mapping)
            or stage.get("schema_version") != STAGE_COMPLETION_RECEIPT_SCHEMA
            or not isinstance(stage.get("stage_identity"), str)
            or not stage["stage_identity"]
        ):
            return _result(status="not_applicable", intent=None)
        receipt = hook_input.get("receipt")
        if not isinstance(receipt, Mapping):
            return _result(status="not_applicable", intent=None)
        receipt_id = str(receipt.get("event_id") or "")
        stage_identity = str(stage["stage_identity"])
        project_progress = (
            projection.get("project_progress")
            if isinstance(projection, Mapping)
            else None
        )
        last_report = (
            projection.get("last_report") if isinstance(projection, Mapping) else None
        )
        intent_payload: dict[str, Any] = {
            "schema_version": "periodic_report_trigger_evaluation_intent_v0",
            "stage_completion": dict(stage),
            "profile_ref": dict(profile_ref or {}),
            "trigger_policy": dict(trigger_policy or {}),
            "generation_authorized": False,
            "external_delivery_authorized": False,
        }
        if isinstance(project_progress, Mapping):
            intent_payload["project_progress"] = dict(project_progress)
        if isinstance(last_report, Mapping):
            intent_payload["last_report"] = dict(last_report)
        observed_capabilities = (
            projection.get("available_capabilities")
            if isinstance(projection, Mapping)
            else None
        )
        if isinstance(observed_capabilities, list) and observed_capabilities:
            intent_payload["available_capabilities"] = list(observed_capabilities)
        return _result(
            status="intent",
            intent={
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT,
                "idempotency_key": f"periodic-report:{stage_identity}",
                "source_receipt_id": receipt_id,
                "payload": intent_payload,
                "requested_write_scope": [],
            },
        )

    return PostWritebackHookRegistration(
        hook_id=PERIODIC_REPORT_POST_WRITEBACK_HOOK_ID,
        capability_id="periodic-report",
        event_kinds=("refresh_state", "todo_complete"),
        intent_kinds=(PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT,),
        requested_read_scope=(
            "stage_completion",
            "project_progress",
            "last_report",
            "available_capabilities",
        ),
        producer=producer,
        policy_version=policy_version,
    )


def evaluate_periodic_report_trigger_evaluation_intent(
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    """Fake-scheduler/governed-executor seam for one recorded trigger intent."""

    if (
        intent.get("schema_version") != "loopx_capability_intent_v0"
        or intent.get("intent_kind") != PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT
        or intent.get("requested_write_scope") != []
    ):
        raise ValueError("periodic-report trigger intent contract is invalid")
    payload = intent.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("periodic-report trigger intent payload is invalid")
    if (
        payload.get("schema_version") != "periodic_report_trigger_evaluation_intent_v0"
        or payload.get("generation_authorized") is not False
        or payload.get("external_delivery_authorized") is not False
    ):
        raise ValueError("periodic-report trigger intent authority is invalid")
    profile_ref = payload.get("profile_ref")
    trigger_policy = payload.get("trigger_policy")
    if not isinstance(profile_ref, Mapping) or not isinstance(
        trigger_policy, Mapping
    ):
        raise ValueError("periodic-report trigger intent is missing typed facts")
    if "cadence_window" in payload:
        from .cadence_journal import validate_cadence_window

        if "report_request" in payload or "stage_completion" in payload:
            raise ValueError("cadence intent cannot also claim a request or stage")
        window = validate_cadence_window(payload["cadence_window"])
        if profile_ref != window["profile_ref"] or trigger_policy != window["trigger_policy"]:
            raise ValueError("cadence profile differs from its frozen window")
        if intent.get("source_receipt_id") != window["window_id"] or intent.get(
            "idempotency_key"
        ) != "periodic-report:" + window["window_id"]:
            raise ValueError("cadence intent identity does not match its window")
        return build_periodic_report_trigger_decision({
            "schema_version": "periodic_report_trigger_request_v0",
            "evaluated_at": window["due_at"],
            "profile": {"profile_id": profile_ref.get("profile_id"),
                        "profile_version": profile_ref.get("profile_version")},
            "trigger_policy": dict(trigger_policy),
            "candidates": [{
                "trigger_kind": "cadence_due", "observed_at": window["due_at"],
                "source_ref": "cadence:" + window["window_id"],
                "evidence_digest": "sha256:" + window["window_id"].removeprefix("cadence_"),
                "facts": {"due": True},
            }],
        })
    report_request = payload.get("report_request")
    if isinstance(report_request, Mapping):
        expected = {
            "schema_version",
            "request_id",
            "goal_id",
            "agent_id",
            "requested_at",
            "source_digest",
            "requester_kind",
            "addressing_source",
        }
        if (
            set(report_request) != expected
            or report_request.get("schema_version") != REQUEST_ACTION_SCHEMA
            or report_request.get("requester_kind") != "user"
            or report_request.get("addressing_source")
            not in {"provider_mention", "verified_reply"}
            or any(
                not str(report_request.get(field) or "").strip()
                for field in (
                    "request_id",
                    "goal_id",
                    "agent_id",
                    "requested_at",
                    "source_digest",
                )
            )
        ):
            raise ValueError("periodic-report typed request is invalid")
        requested_at = str(report_request["requested_at"])
        request_id = str(report_request["request_id"])
        evidence_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                {
                    "request_id": request_id,
                    "goal_id": report_request["goal_id"],
                    "agent_id": report_request["agent_id"],
                    "source_digest": report_request["source_digest"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return build_periodic_report_trigger_decision(
            {
                "schema_version": "periodic_report_trigger_request_v0",
                "evaluated_at": requested_at,
                "profile": {
                    "profile_id": profile_ref.get("profile_id"),
                    "profile_version": profile_ref.get("profile_version"),
                },
                "trigger_policy": dict(trigger_policy),
                "candidates": [
                    {
                        "trigger_kind": "manual",
                        "observed_at": requested_at,
                        "source_ref": f"request:{request_id}",
                        "evidence_digest": evidence_digest,
                        "facts": {"authorized": True},
                    }
                ],
            }
        )

    stage = payload.get("stage_completion")
    if not isinstance(stage, Mapping):
        raise ValueError("periodic-report trigger intent is missing typed facts")
    required_stage_fields = (
        "stage_identity",
        "closed_vision_revision",
        "frontier_identity",
        "transition",
        "completed_at",
    )
    if (
        stage.get("schema_version") != STAGE_COMPLETION_RECEIPT_SCHEMA
        or stage.get("acceptance") != "validated"
        or stage.get("outcome_checkpoint_satisfied") is not True
        or any(
            not str(stage.get(field) or "").strip() for field in required_stage_fields
        )
    ):
        raise ValueError("periodic-report stage completion receipt is invalid")
    completed_at = str(stage["completed_at"])
    stage_identity = str(stage["stage_identity"])
    evidence_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps([stage_identity], separators=(",", ":")).encode()
        ).hexdigest()
    )
    request = {
        "schema_version": "periodic_report_trigger_request_v0",
        "evaluated_at": completed_at,
        "profile": {
            "profile_id": profile_ref.get("profile_id"),
            "profile_version": profile_ref.get("profile_version"),
        },
        "trigger_policy": dict(trigger_policy),
        "candidates": [
            {
                "trigger_kind": "bounded_segment_milestone",
                "observed_at": completed_at,
                "source_ref": f"stage:{stage_identity}",
                "evidence_digest": evidence_digest,
                "facts": {
                    "segment_ref": stage_identity,
                    "transition": "segment_completed",
                    "delivered_count": 0,
                    "remaining_todo_count": 0,
                    "durable_writeback": True,
                    "acceptance": "validated",
                    "completed_at": completed_at,
                    "completion_receipt_ref": f"stage:{stage_identity}",
                    "stage_identity": stage_identity,
                    "closed_vision_revision": stage["closed_vision_revision"],
                    "frontier_identity": stage["frontier_identity"],
                    "stage_transition": stage["transition"],
                    "outcome_checkpoint_satisfied": True,
                    "status": "completed",
                },
            }
        ],
    }
    if isinstance(payload.get("last_report"), Mapping):
        request["last_report"] = dict(payload["last_report"])
    return build_periodic_report_trigger_decision(request)


__all__ = [
    "build_periodic_report_post_writeback_projection",
    "evaluate_periodic_report_trigger_evaluation_intent",
    "PERIODIC_REPORT_POST_WRITEBACK_HOOK_ID",
    "PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT",
    "periodic_report_post_writeback_hook",
    "periodic_report_post_writeback_hooks_for_goal",
]
