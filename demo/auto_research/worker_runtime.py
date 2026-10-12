from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .defaults import (
    AUTO_RESEARCH_DEFAULT_GOAL_ID,
    AUTO_RESEARCH_DEFAULT_OBJECTIVE,
)
from .live_evidence import (
    LIVE_CODEX_E2E_DEFAULT_OUTPUT,
)
from .preset import (
    auto_research_role_id_for_action,
    auto_research_successor_specs_for_action,
)
from .research_state import (
    build_live_auto_research_projection,
    build_research_decision_candidates,
    build_research_evidence_graph_from_rollout_events,
    build_research_failure_continuation_resolution,
    normalize_auto_research_action,
)
from ..multi_agent.role_successor import (
    apply_role_successor_todos,
    first_successor_followup,
)
from loopx.control_plane.todos.contract import (
    normalize_supported_todo_resume_when,
    normalize_todo_id,
)
from loopx.control_plane.todos.handoff_mode import HANDOFF_MODE_HARD_LEASE
from loopx.control_plane.work_items.task_lease import (
    TaskLeaseError,
    acquire_task_lease,
    inspect_task_lease,
    release_task_lease,
    runtime_root_from_registry,
)
from loopx.history import load_registry
from loopx.paths import resolve_runtime_root
from loopx.quota import build_quota_should_run
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from loopx.status import collect_status
from loopx.todos import complete_goal_todo


AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION = "auto_research_worker_turn_v0"
AUTO_RESEARCH_WORKER_FRONTIER_SCHEMA_VERSION = "auto_research_worker_frontier_v0"
SUPPORTED_WORKER_ACTIONS = {
    "write_research_contract",
    "review_research_contract",
    "propose_hypothesis",
    "review_hypothesis_frontier",
    "run_dev_eval",
    "run_holdout_eval",
    "remediate_data_measurement",
    "propose_failure_successor",
    "write_evidence",
    "classify_evidence",
    "summarize_evidence",
    "write_evaluation_summary",
    "review_promotion_readiness",
    "record_terminal_decision",
    "review_terminal_decision",
}
AppendEvidence = Callable[[str], dict[str, object]]
AUTO_RESEARCH_STATE_SUMMARY_MODE = "rollout_evidence_summary"
AUTO_RESEARCH_MANUAL_RESEARCH_REQUIRED_MODE = "manual_research_required"
MANUAL_RESEARCH_REQUIRED_ACTIONS = {
    "write_research_contract",
    "propose_hypothesis",
    "propose_failure_successor",
    "run_dev_eval",
    "run_holdout_eval",
    "remediate_data_measurement",
    "write_evidence",
    "record_terminal_decision",
    "review_terminal_decision",
}
SUMMARY_ACTIONS = {
    "classify_evidence",
    "summarize_evidence",
    "write_evaluation_summary",
    "review_research_contract",
    "review_hypothesis_frontier",
    "review_promotion_readiness",
}


def _scored_rollout_metric_events(
    rollout_events: list[dict[str, object]],
    *,
    split: str | None = None,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for event in rollout_events:
        if str(event.get("event_kind") or "") != "research_evidence":
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if split is not None and str(details.get("split") or "") != split:
            continue
        if details.get("metric_value") is None:
            continue
        events.append(event)
    return events


def _holdout_metric_sequence(rollout_events: list[dict[str, object]]) -> list[float]:
    sequence: list[float] = []
    for event in _scored_rollout_metric_events(rollout_events, split="holdout"):
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        value = details.get("metric_value")
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            sequence.append(float(value))
    return sequence


def _holdout_improvement_count(
    holdout_metrics: list[float],
    *,
    baseline: float | None,
    direction: str,
) -> int:
    if baseline is None:
        return 0
    count = 0
    previous = baseline
    for metric in holdout_metrics:
        improved = metric < previous if direction == "minimize" else metric > previous
        if improved:
            count += 1
        previous = metric
    return count


def _has_rollout_research_evidence(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
) -> bool:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    return any(
        str(event.get("event_kind") or "") == "research_evidence"
        for event in load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    )


def _slug(value: object, *, default: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return text[:80] or default


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact_summary(kind: str, *, filename: str) -> dict[str, object]:
    return {
        "kind": kind,
        "filename": filename,
        "paths_are_local_only": True,
    }


def _write_evaluation_summary_artifact(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_path: Path,
    goal_id: str,
    todo_id: str,
    lineage_todo_id: str | None,
    require_selected_failure_match: bool,
    agent_id: str,
) -> dict[str, object]:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    rollout_events = load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    graph = build_research_evidence_graph_from_rollout_events(
        goal_id=goal_id,
        rollout_events=rollout_events,
    )
    decisions = build_research_decision_candidates(graph)
    failure_resolution = build_research_failure_continuation_resolution(
        decisions,
        selected_todo_id=todo_id,
        selected_lineage_todo_id=lineage_todo_id,
        require_selected_failure_match=require_selected_failure_match,
    )
    failure_continuation = failure_resolution["failure_continuation"]
    failure_continuation_unresolved = bool(failure_resolution["unresolved"])
    holdout_metrics = _holdout_metric_sequence(rollout_events)
    metric = graph.get("metric") if isinstance(graph.get("metric"), dict) else {}
    baseline = graph.get("baseline_metric")
    holdout_improvements = _holdout_improvement_count(
        holdout_metrics,
        baseline=float(baseline) if isinstance(baseline, (int, float)) else None,
        direction=str(metric.get("direction") or "maximize"),
    )
    dev_pending_holdout_count = len(decisions.get("dev_promotion_candidates") or [])
    artifact = {
        "ok": True,
        "schema_version": "auto_research_worker_evaluation_summary_v0",
        "goal_id": goal_id,
        "todo_id": todo_id,
        "agent_id": agent_id,
        "evidence_graph_summary": {
            "event_count": graph.get("event_count"),
            "hypothesis_count": graph.get("hypothesis_count"),
            "best_dev_metric": graph.get("best_dev_metric"),
            "best_holdout_metric": graph.get("best_holdout_metric"),
            "holdout_improved": graph.get("holdout_improved"),
            "holdout_metric_sequence": holdout_metrics,
            "holdout_improvement_count": holdout_improvements,
            "negative_evidence_count": graph.get("negative_evidence_count"),
        },
        "decision_summary": {
            "dev_promotion_candidate_count": len(decisions.get("dev_promotion_candidates") or []),
            "dev_candidate_pending_holdout_count": dev_pending_holdout_count,
            "validated_promotion_candidate_count": len(decisions.get("validated_promotion_candidates") or []),
            "promotion_candidate_count": len(decisions.get("promotion_candidates") or []),
            "retirement_candidate_count": len(decisions.get("retirement_candidates") or []),
            "failure_continuation": failure_continuation,
            "failure_continuation_unresolved": failure_continuation_unresolved,
            "holdout_metric_sequence": holdout_metrics,
            "holdout_improvement_count": holdout_improvements,
        },
        "summary": {
            "status": "evaluation_summary_written",
            "claim_allowed": bool(decisions.get("validated_promotion_candidates")),
            "promotion_decision_made": False,
        },
        "public_boundary": {
            "source": "loopx_rollout_event_log",
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
        },
    }
    _write_json(output_path, artifact)
    return artifact


def _selected_failure_lineage_todo_id(selected: dict[str, object]) -> str | None:
    unblocks_todo_id = normalize_todo_id(selected.get("unblocks_todo_id"))
    if unblocks_todo_id:
        return unblocks_todo_id
    resume_when = normalize_supported_todo_resume_when(selected.get("resume_when"))
    if not resume_when:
        return None
    kind, _separator, target = resume_when.partition(":")
    return normalize_todo_id(target) if kind == "todo_done" else None


def _failure_lineage_unresolved_result(
    *,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    action: str,
    artifact: dict[str, object],
    frontier_packet: dict[str, object],
    complete_selected_todo: bool,
) -> dict[str, object]:
    decision_summary = artifact["decision_summary"]
    return {
        "ok": True,
        "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
        "mode": "execute",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "selected_todo_id": todo_id,
        "selected_action": action,
        "executed": True,
        "summary_mode": AUTO_RESEARCH_STATE_SUMMARY_MODE,
        "artifact": _artifact_summary("evaluation_summary", filename="evaluation-summary.public.json"),
        "artifact_status": artifact["summary"]["status"],
        "claim_allowed": artifact["summary"]["claim_allowed"],
        "promotion_decision_made": artifact["summary"]["promotion_decision_made"],
        "role_id": auto_research_role_id_for_action(action),
        "evaluation_summary": {
            "claim_allowed": artifact["summary"]["claim_allowed"],
            "best_dev_metric": artifact["evidence_graph_summary"]["best_dev_metric"],
            "best_holdout_metric": artifact["evidence_graph_summary"]["best_holdout_metric"],
            "holdout_metric_sequence": artifact["evidence_graph_summary"][
                "holdout_metric_sequence"
            ],
            "holdout_improvement_count": decision_summary["holdout_improvement_count"],
            "validated_promotion_candidate_count": decision_summary[
                "validated_promotion_candidate_count"
            ],
            "failure_continuation": None,
        },
        "successor_todos": {
            "schema_version": "multi_agent_role_successor_todos_v0",
            "source": "role_profile_todo_command_template",
            "needed": False,
            "executed": False,
            "role_id": auto_research_role_id_for_action(action),
            "action": action,
            "successors": [],
            "skipped": [],
            "reason": "failure_successor_lineage_unresolved",
        },
        "followup": None,
        "completion": {
            "requested": complete_selected_todo,
            "executed": False,
            "reason": "failure_successor_lineage_unresolved",
        },
        "frontier": frontier_packet,
        "public_boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "credentials_recorded": False,
        },
    }


def _manual_research_required_result(
    *,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    action: str,
    complete_selected_todo: bool,
    frontier_packet: dict[str, object],
) -> dict[str, object]:
    role_id = auto_research_role_id_for_action(action)
    return {
        "ok": True,
        "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
        "mode": AUTO_RESEARCH_MANUAL_RESEARCH_REQUIRED_MODE,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "selected_todo_id": todo_id,
        "selected_action": action,
        "role_id": role_id,
        "executed": False,
        "manual_research_required": True,
        "reason": "auto_research_visible_roles_must_author_real_evidence",
        "next_steps": [
            "read the role profile and selected frontier",
            "perform the role's research work in the visible Codex TUI",
            "write public-safe contract, hypothesis, evidence, or todo handoff through LoopX state",
            "do not claim dev/holdout uplift unless it comes from an explicit evidence packet or real evaluator output",
        ],
        "completion": {
            "requested": complete_selected_todo,
            "executed": False,
            "reason": "manual_research_required_before_completion",
        },
        "frontier": frontier_packet,
        "public_boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "credentials_recorded": False,
            "fake_metrics_recorded": False,
        },
    }


def _maybe_add_role_successor_todos(
    *,
    registry_path: Path,
    goal_id: str,
    source_todo_id: str,
    agent_id: str,
    role_id: str,
    action: str,
    decision_summary: dict[str, object],
    execute: bool,
) -> dict[str, object]:
    successor_specs = auto_research_successor_specs_for_action(
        role_id=role_id,
        action=action,
    )
    failure_continuation = decision_summary.get("failure_continuation")
    if isinstance(failure_continuation, dict):
        next_outcome = str(failure_continuation.get("next_outcome") or "")
        successor_specs = [
            spec
            for spec in successor_specs
            if spec.get("action_kind") == next_outcome
        ]
    text_source_todo_id = None
    if isinstance(failure_continuation, dict):
        text_source_todo_id = str(failure_continuation.get("source_todo_id") or "") or None
    return apply_role_successor_todos(
        registry_path=registry_path,
        goal_id=goal_id,
        source_todo_id=source_todo_id,
        current_agent_id=agent_id,
        role_id=role_id,
        action=action,
        successor_specs=successor_specs,
        decision_summary=decision_summary,
        execute=execute,
        text_source_todo_id=text_source_todo_id,
    )


def _executed_successor_todo_ids(successor_todos: dict[str, object]) -> list[str]:
    successors = successor_todos.get("successors")
    if not isinstance(successors, list):
        return []
    todo_ids: list[str] = []
    for successor in successors:
        if not isinstance(successor, dict):
            continue
        todo_id = successor.get("todo_id")
        if isinstance(todo_id, str) and todo_id:
            todo_ids.append(todo_id)
    return todo_ids


def _complete_selected_todo(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    action: str,
    execute: bool,
    successor_todo_ids: list[str] | None = None,
    lease_proof: dict[str, object] | None = None,
) -> dict[str, object]:
    if not execute:
        return {"requested": True, "executed": False}
    linked_successors = successor_todo_ids or []
    result = complete_goal_todo(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        goal_id=goal_id,
        todo_id=todo_id,
        role="agent",
        claimed_by=agent_id,
        agent_id=agent_id,
        note=f"auto-research state summary completed {action}",
        evidence=(
            f"state-summary agent={agent_id} action={action} wrote public-safe local artifact "
            "and obeyed quota/frontier before completion"
        ),
        no_followup=not linked_successors,
        successor_todo_ids=linked_successors or None,
        self_merged=True,
        dry_run=False,
        **(lease_proof or {}),
    )
    return {
        "requested": True,
        "executed": True,
        "ok": bool(result.get("ok")),
        "changed": bool(result.get("changed")),
        "todo_id": result.get("todo_id"),
        "status": "done" if result.get("completed") else None,
        "self_merged": bool(result.get("self_merged")),
        "successor_todo_ids": result.get("successor_todo_ids") or linked_successors,
    }


@contextmanager
def _worker_execution_lease(
    *, registry_path: Path, runtime_root_arg: str | None,
    goal_id: str, todo_id: str, agent_id: str,
) -> Iterator[dict[str, object]]:
    """Use the typed lease owner without borrowing another execution's fence."""
    runtime_root = runtime_root_from_registry(registry_path, runtime_root_arg)
    current = inspect_task_lease(
        registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, todo_id=todo_id,
    )
    if current.get("ok") is not True:
        raise TaskLeaseError("Worker lease inspection failed", code=str(current.get("error_code")))
    if current.get("handoff_mode") != HANDOFF_MODE_HARD_LEASE:
        yield {}
        return
    key = "auto-research-worker-" + uuid4().hex
    acquired = acquire_task_lease(
        registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, todo_id=todo_id, owner=agent_id,
        idempotency_key=key,
    )
    version = acquired["lease"]["version"]
    try:
        yield {"task_lease_idempotency_key": key, "task_lease_expected_version": version}
    finally:
        # Completion may already have retired this execution. Cleanup must never
        # release a newer lease acquired by another invocation.
        current = inspect_task_lease(
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, todo_id=todo_id,
        )
        if current.get("ok") is not True:
            raise TaskLeaseError("Worker lease cleanup readback failed", code=str(current.get("error_code")))
        lease = current.get("lease") or {}
        if (current.get("active") is True and lease.get("owner") == agent_id
            and lease.get("idempotency_key") == key and lease.get("version") == version):
            release_task_lease(
                registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, todo_id=todo_id, owner=agent_id,
                idempotency_key=key, expected_version=version,
            )


def load_auto_research_worker_frontier(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str,
    workspace: Path,
) -> dict[str, object]:
    """Read the same quota/frontier surfaces a visible worker must obey."""

    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
        scan_roots=[workspace],
        limit=5,
        goal_id=goal_id,
    )
    quota_payload = build_quota_should_run(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    projection = build_live_auto_research_projection(
        goal_id=goal_id,
        agent_id=agent_id,
        quota_payload=quota_payload,
        rollout_events=load_rollout_events(rollout_event_log_path(runtime_root, goal_id)),
    )
    frontier = projection["frontier"]
    selected = frontier.get("selected") if isinstance(frontier, dict) else None
    completion = frontier.get("completion") if isinstance(frontier, dict) else None
    return {
        "ok": True,
        "schema_version": AUTO_RESEARCH_WORKER_FRONTIER_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "quota": {
            "ok": bool(quota_payload.get("ok")),
            "should_run": bool(quota_payload.get("should_run")),
            "state": quota_payload.get("state"),
            "user_action_required": bool(
                ((quota_payload.get("interaction_contract") or {}).get("user_channel") or {}).get(
                    "action_required"
                )
            ),
        },
        "frontier": {
            "selected": selected,
            "runnable_count": len(frontier.get("runnable") or []) if isinstance(frontier, dict) else 0,
            "blocked_count": len(frontier.get("blocked") or []) if isinstance(frontier, dict) else 0,
            "source_kind": frontier.get("source_kind") if isinstance(frontier, dict) else None,
            "completion": completion if isinstance(completion, dict) else None,
        },
        "public_boundary": {
            "source": "loopx_quota_and_auto_research_frontier",
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
        },
    }


def run_auto_research_worker_turn(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str = AUTO_RESEARCH_DEFAULT_GOAL_ID,
    agent_id: str,
    objective: str = AUTO_RESEARCH_DEFAULT_OBJECTIVE,
    workspace: Path,
    output_dir: str = "auto_research_lightweight_kernel",
    evidence_dir: str = ".local/auto-research-worker",
    execute: bool = False,
    append_evidence: AppendEvidence | None = None,
    lane_count: int = 1,
    visible_lanes_accepted: bool = False,
    live_evidence_output: str = LIVE_CODEX_E2E_DEFAULT_OUTPUT,
    complete_selected_todo: bool = False,
) -> dict[str, object]:
    """Run one LoopX-selected visible worker action.

    The worker does not choose its own work. It reads quota/frontier, checks the
    selected action, then performs one small public-safe research turn.
    """

    workspace = workspace.resolve()
    frontier_packet = load_auto_research_worker_frontier(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        goal_id=goal_id,
        agent_id=agent_id,
        workspace=workspace,
    )
    selected = frontier_packet["frontier"].get("selected") if isinstance(frontier_packet["frontier"], dict) else None
    raw_action = str((selected or {}).get("allowed_action") or "")
    action = normalize_auto_research_action(raw_action)
    todo_id = str((selected or {}).get("todo_id") or "")
    lineage_todo_id = (
        _selected_failure_lineage_todo_id(selected)
        if isinstance(selected, dict)
        else None
    )
    if not selected or not todo_id:
        completion = (
            frontier_packet["frontier"].get("completion")
            if isinstance(frontier_packet.get("frontier"), dict)
            else None
        )
        if isinstance(completion, dict) and completion.get("quiet_completion_allowed") is True:
            return {
                "ok": True,
                "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
                "mode": "quiet_completion",
                "goal_id": goal_id,
                "agent_id": agent_id,
                "executed": False,
                "completion": completion,
                "frontier": frontier_packet,
                "public_boundary": {
                    "raw_logs_recorded": False,
                    "private_artifacts_recorded": False,
                    "absolute_paths_recorded": False,
                    "credentials_recorded": False,
                },
            }
        return {
            "ok": True,
            "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
            "mode": "no_action",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "executed": False,
            "frontier": frontier_packet,
        }
    quota_ok = bool(frontier_packet.get("quota", {}).get("ok"))
    quota_should_run = bool(frontier_packet.get("quota", {}).get("should_run"))
    if quota_ok and not quota_should_run:
        return {
            "ok": True,
            "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
            "mode": "paused_by_quota",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "selected_todo_id": todo_id,
            "selected_action": action,
            "executed": False,
            "quota": frontier_packet.get("quota"),
            "frontier": frontier_packet,
            "public_boundary": {
                "raw_logs_recorded": False,
                "private_artifacts_recorded": False,
                "absolute_paths_recorded": False,
                "credentials_recorded": False,
            },
        }
    if action not in SUPPORTED_WORKER_ACTIONS:
        return {
            "ok": True,
            "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
            "mode": "unsupported_action",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "selected_todo_id": todo_id,
            "selected_action": action,
            "supported_actions": sorted(SUPPORTED_WORKER_ACTIONS),
            "executed": False,
            "frontier": frontier_packet,
        }
    if not execute:
        return {
            "ok": True,
            "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
            "mode": "dry_run",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "selected_todo_id": todo_id,
            "selected_action": action,
            "would_execute": action,
            "completion": {"requested": complete_selected_todo, "executed": False},
            "frontier": frontier_packet,
        }
    if action in MANUAL_RESEARCH_REQUIRED_ACTIONS:
        return _manual_research_required_result(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            action=action,
            complete_selected_todo=complete_selected_todo,
            frontier_packet=frontier_packet,
        )

    run_dir = workspace / evidence_dir / _slug(agent_id, default="agent") / _slug(todo_id, default="todo")
    evaluation_summary_path = run_dir / "evaluation-summary.public.json"

    if action in SUMMARY_ACTIONS:
        if not _has_rollout_research_evidence(
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_id=goal_id,
        ):
            return _manual_research_required_result(
                goal_id=goal_id,
                agent_id=agent_id,
                todo_id=todo_id,
                action=action,
                complete_selected_todo=complete_selected_todo,
                frontier_packet=frontier_packet,
            )
        frontier_completion = (
            frontier_packet["frontier"].get("completion")
            if isinstance(frontier_packet.get("frontier"), dict)
            else None
        )
        require_selected_failure_match = bool(
            isinstance(frontier_completion, dict)
            and frontier_completion.get("status") == "failure_successor_required"
        )
        with _worker_execution_lease(
            registry_path=registry_path, runtime_root_arg=runtime_root_arg,
            goal_id=goal_id, todo_id=todo_id, agent_id=agent_id,
        ) as lease_proof:
            artifact = _write_evaluation_summary_artifact(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                output_path=evaluation_summary_path,
                goal_id=goal_id,
                todo_id=todo_id,
                lineage_todo_id=lineage_todo_id,
                require_selected_failure_match=require_selected_failure_match,
                agent_id=agent_id,
            )
            if artifact["decision_summary"]["failure_continuation_unresolved"]:
                return _failure_lineage_unresolved_result(
                    goal_id=goal_id,
                    agent_id=agent_id,
                    todo_id=todo_id,
                    action=action,
                    artifact=artifact,
                    frontier_packet=frontier_packet,
                    complete_selected_todo=complete_selected_todo,
                )
            role_id = auto_research_role_id_for_action(action)
            successor_todos = _maybe_add_role_successor_todos(
                registry_path=registry_path,
                goal_id=goal_id,
                source_todo_id=todo_id,
                agent_id=agent_id,
                role_id=role_id,
                action=action,
                decision_summary=artifact["decision_summary"],
                execute=True,
            )
            successor_todo_ids = _executed_successor_todo_ids(successor_todos)
            followup = first_successor_followup(successor_todos)
            completion = (
                _complete_selected_todo(
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    goal_id=goal_id,
                    todo_id=todo_id,
                    agent_id=agent_id,
                    action=action,
                    execute=True,
                    successor_todo_ids=successor_todo_ids,
                    lease_proof=lease_proof,
                )
                if complete_selected_todo
                else {"requested": False}
            )
            return {
                "ok": True,
                "schema_version": AUTO_RESEARCH_WORKER_TURN_SCHEMA_VERSION,
                "mode": "execute",
                "goal_id": goal_id,
                "agent_id": agent_id,
                "selected_todo_id": todo_id,
                "selected_action": action,
                "executed": True,
                "summary_mode": AUTO_RESEARCH_STATE_SUMMARY_MODE,
                "artifact": _artifact_summary("evaluation_summary", filename="evaluation-summary.public.json"),
                "artifact_status": artifact["summary"]["status"],
                "claim_allowed": artifact["summary"]["claim_allowed"],
                "promotion_decision_made": artifact["summary"]["promotion_decision_made"],
                "role_id": role_id,
                "evaluation_summary": {
                    "claim_allowed": artifact["summary"]["claim_allowed"],
                    "best_dev_metric": artifact["evidence_graph_summary"]["best_dev_metric"],
                    "best_holdout_metric": artifact["evidence_graph_summary"]["best_holdout_metric"],
                    "holdout_metric_sequence": artifact["evidence_graph_summary"][
                        "holdout_metric_sequence"
                    ],
                    "holdout_improvement_count": artifact["decision_summary"][
                        "holdout_improvement_count"
                    ],
                    "validated_promotion_candidate_count": artifact["decision_summary"][
                        "validated_promotion_candidate_count"
                    ],
                    "failure_continuation": artifact["decision_summary"][
                        "failure_continuation"
                    ],
                },
                "successor_todos": successor_todos,
                "followup": followup,
                "completion": completion,
                "frontier": frontier_packet,
                "public_boundary": {
                    "raw_logs_recorded": False,
                    "private_artifacts_recorded": False,
                    "absolute_paths_recorded": False,
                    "credentials_recorded": False,
                },
            }

    return _manual_research_required_result(
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        action=action,
        complete_selected_todo=complete_selected_todo,
        frontier_packet=frontier_packet,
    )
