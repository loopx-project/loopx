from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from .agent_registry import registered_agent_ids_from_registry
from .control_plane.runtime.public_safety import public_safe_compact_text
from .control_plane.runtime.shared_runtime_material_projection import (
    finalize_material_projection,
    prepare_material_projection_route,
)
from .control_plane.runtime.status_classifications import (
    DREAMING_ADVISORY_CLASSIFICATIONS,
)
from .feedback import validate_local_control_text, validate_public_safe_text
from .history import (
    STATUS_NEUTRAL_CLASSIFICATIONS,
    collect_history,
    load_registry,
    write_reserved_run_artifacts,
)
from .paths import resolve_runtime_root
from .registry import registry_goals
from .runtime import validate_goal_id_path_segment
from .state_refresh import now_local
from .control_plane.todos.mutation_api import add_goal_todo, update_goal_todo

DREAMING_DRY_RUN_SCHEMA_VERSION = "dreaming_dry_run_proposal_v0"
DREAMING_PROPOSAL_SCHEMA_VERSION = "dreaming_proposal_v0"
DREAMING_PROPOSAL_DECISION_SCHEMA_VERSION = "dreaming_proposal_decision_v0"
SERVER_PLANNING_CONTRACT_SCHEMA_VERSION = "server_managed_planning_contract_v0"
MAX_DREAMING_EVIDENCE_ITEMS = 5
DREAMING_PROPOSAL_DECISIONS = {"approve", "defer", "reject"}


def _compact_run(run: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for field in ("generated_at", "classification"):
        value = public_safe_compact_text(run.get(field), limit=120)
        if value:
            compact[field] = value
    action = public_safe_compact_text(
        run.get("recommended_action") or run.get("summary") or run.get("health_check"),
        limit=220,
    )
    if action:
        compact["recommended_action"] = action
    outcome = public_safe_compact_text(run.get("delivery_outcome"), limit=80)
    if outcome:
        compact["delivery_outcome"] = outcome
    return compact


def _goal_record(history_payload: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    for goal in history_payload.get("goals") or []:
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            return goal
    return None


def _signal_runs(goal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    latest_runs = goal.get("latest_runs") if isinstance(goal.get("latest_runs"), list) else []
    signal_runs: list[dict[str, Any]] = []
    for run in latest_runs:
        if not isinstance(run, dict):
            continue
        classification = str(run.get("classification") or "")
        if not classification:
            continue
        if classification in STATUS_NEUTRAL_CLASSIFICATIONS:
            continue
        if classification in DREAMING_ADVISORY_CLASSIFICATIONS:
            continue
        signal_runs.append(run)
        if len(signal_runs) >= limit:
            break
    return signal_runs


def _proposal_type(runs: list[dict[str, Any]]) -> str:
    compact_signals: list[str] = []
    for run in runs:
        compact = public_safe_compact_text(
            " ".join(
                str(run.get(field) or "")
                for field in (
                    "classification",
                    "recommended_action",
                    "health_check",
                    "delivery_outcome",
                )
            ),
            limit=500,
        )
        if compact:
            compact_signals.append(compact)
    combined = " ".join(compact_signals).lower()
    if any(token in combined for token in ("refactor", "duplicate", "bloat", "large", "monolith", "drift")):
        return "refactor_warning"
    if any(token in combined for token in ("lesson", "memory", "playbook", "skill", "docs", "documentation")):
        return "memory_consolidation"
    if any(token in combined for token in ("archive", "obsolete", "stale")):
        return "archive_suggestion"
    return "exploration"


def _classification_for_proposal_type(proposal_type: str) -> str:
    return {
        "refactor_warning": "dreaming_refactor_warning",
        "memory_consolidation": "dreaming_memory_consolidation",
        "archive_suggestion": "dreaming_archive_suggestion",
    }.get(proposal_type, "dreaming_exploration_proposal")


def _operator_question(goal_id: str, proposal_type: str) -> str:
    if proposal_type == "refactor_warning":
        return (
            f"Should {goal_id} open a reviewed delivery todo for the repeated "
            "refactor or state-drift warning found in recent run history?"
        )
    if proposal_type == "memory_consolidation":
        return (
            f"Should {goal_id} consolidate these repeated lessons into a "
            "project-local playbook, skill, or active-state update?"
        )
    if proposal_type == "archive_suggestion":
        return (
            f"Should {goal_id} review whether stale or obsolete work should be "
            "archived before the next delivery slice?"
        )
    return (
        f"Should {goal_id} promote this exploration proposal into a concrete "
        "delivery todo, defer it, or reject it?"
    )


def _proposal_summary(runs: list[dict[str, Any]], proposal_type: str) -> str:
    classifications = Counter(str(run.get("classification") or "unknown") for run in runs)
    top = ", ".join(f"{name} x{count}" for name, count in classifications.most_common(3))
    if not top:
        top = "no recent non-neutral run history"
    if proposal_type == "refactor_warning":
        return f"Recent run history suggests a possible refactor/state-drift warning: {top}."
    if proposal_type == "memory_consolidation":
        return f"Recent run history has repeated lessons worth consolidating: {top}."
    if proposal_type == "archive_suggestion":
        return f"Recent run history may contain stale work that needs archive review: {top}."
    return f"Recent run history suggests an exploration option for operator review: {top}."


def _proposal_id(
    *,
    goal_id: str,
    proposal_type: str,
    evidence_window: str,
    runs: list[dict[str, Any]],
) -> str:
    seed_parts = [goal_id, proposal_type, evidence_window]
    for run in runs[:MAX_DREAMING_EVIDENCE_ITEMS]:
        seed_parts.append(str(run.get("generated_at") or ""))
        seed_parts.append(str(run.get("classification") or ""))
    digest = hashlib.sha256("\n".join(seed_parts).encode("utf-8")).hexdigest()[:12]
    return f"dreaming_{digest}"


def _registry_goal(registry: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    for goal in registry_goals(registry):
        if str(goal.get("id") or "") == goal_id:
            return goal
    return None


def _compact_source_proposal(run: dict[str, Any]) -> dict[str, Any]:
    dreaming = run.get("dreaming") if isinstance(run.get("dreaming"), dict) else {}
    return {
        "generated_at": public_safe_compact_text(run.get("generated_at"), limit=120),
        "classification": public_safe_compact_text(run.get("classification"), limit=120),
        "proposal_id": public_safe_compact_text(dreaming.get("proposal_id"), limit=120),
        "proposal_type": public_safe_compact_text(dreaming.get("proposal_type"), limit=120),
        "evidence_window": public_safe_compact_text(dreaming.get("evidence_window"), limit=160),
        "summary": public_safe_compact_text(run.get("summary") or run.get("recommended_action"), limit=260),
    }


def _find_source_proposal(
    history_payload: dict[str, Any],
    *,
    goal_id: str,
    proposal_id: str,
) -> dict[str, Any] | None:
    goal = _goal_record(history_payload, goal_id)
    if not goal:
        return None
    latest_runs = goal.get("latest_runs") if isinstance(goal.get("latest_runs"), list) else []
    for run in latest_runs:
        if not isinstance(run, dict):
            continue
        dreaming = run.get("dreaming") if isinstance(run.get("dreaming"), dict) else {}
        if str(dreaming.get("proposal_id") or "") == proposal_id:
            return run
    return None


def build_server_managed_planning_contract() -> dict[str, Any]:
    """Return the default contract for server-managed planning proposals."""

    return {
        "schema_version": SERVER_PLANNING_CONTRACT_SCHEMA_VERSION,
        "lane": "dreaming_planning",
        "authority": "proposal_only_until_promoted",
        "may_rank_candidate_todos": True,
        "may_suggest_evidence_probes": True,
        "may_emit_refactor_warnings": True,
        "may_execute_protected_actions": False,
        "may_read_private_material": False,
        "may_mutate_active_state": False,
        "may_append_delivery_history": False,
        "may_spend_delivery_quota": False,
        "promotion_required": True,
        "promotion_requirements": [
            "operator_or_controller_approval",
            "normal_quota_should_run_decision",
            "goal_boundary_write_scope_approval",
            "public_private_boundary_scan_for_public_artifacts",
        ],
        "allowed_outputs": [
            "ranked_candidate_todos",
            "evidence_probe_suggestions",
            "refactor_warnings",
            "memory_consolidation_proposals",
        ],
        "forbidden_outputs": [
            "agent_command",
            "protected_action_execution",
            "private_material_read",
            "delivery_quota_spend",
            "active_state_mutation_without_promotion",
        ],
    }


def build_dreaming_dry_run_proposal(
    history_payload: dict[str, Any],
    *,
    goal_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Build a local-only dreaming proposal preview from compact run history.

    The returned payload is intentionally advisory: it does not append runtime
    history, mutate active project truth, grant an agent command, or spend quota.
    """

    safe_limit = max(1, min(int(limit), 50))
    goal = _goal_record(history_payload, goal_id)
    if not goal:
        return {
            "ok": False,
            "schema_version": DREAMING_DRY_RUN_SCHEMA_VERSION,
            "goal_id": goal_id,
            "dry_run": True,
            "error": f"goal_id not found in history payload: {goal_id}",
            "side_effects": {
                "project_files_mutated": False,
                "active_state_mutated": False,
                "runtime_history_appended": False,
                "quota_spent": False,
            },
        }

    runs = _signal_runs(goal, safe_limit)
    proposal_type = _proposal_type(runs)
    classification = _classification_for_proposal_type(proposal_type)
    evidence_window = f"last_{len(runs)}_non_neutral_runs" if runs else "no_recent_non_neutral_runs"
    proposal_id = _proposal_id(
        goal_id=goal_id,
        proposal_type=proposal_type,
        evidence_window=evidence_window,
        runs=runs,
    )
    question = _operator_question(goal_id, proposal_type)
    server_planning_contract = build_server_managed_planning_contract()
    dreaming = {
        "schema_version": DREAMING_PROPOSAL_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "lane": "exploration",
        "evidence_window": evidence_window,
        "proposal_type": proposal_type,
        "confidence": "medium" if len(runs) >= 3 else "low",
        "requires_project_controller": True,
        "advisory": True,
        "promoted_to_delivery": False,
        "execution_allowed": False,
        "delivery_spend_allowed": False,
        "server_planning_contract": server_planning_contract,
    }
    preview = {
        "goal_id": goal_id,
        "classification": classification,
        "recommended_action": (
            "Review this advisory dreaming proposal; approve, defer, or reject "
            "it before converting it into active project truth."
        ),
        "operator_question": question,
        "agent_command": None,
        "dreaming": dreaming,
    }
    return {
        "ok": True,
        "schema_version": DREAMING_DRY_RUN_SCHEMA_VERSION,
        "goal_id": goal_id,
        "dry_run": True,
        "proposal_id": proposal_id,
        "classification": classification,
        "proposal_type": proposal_type,
        "summary": _proposal_summary(runs, proposal_type),
        "operator_question": question,
        "recommended_action": preview["recommended_action"],
        "run_record_preview": preview,
        "server_planning_contract": server_planning_contract,
        "recent_evidence": [_compact_run(run) for run in runs[:MAX_DREAMING_EVIDENCE_ITEMS]],
        "side_effects": {
            "project_files_mutated": False,
            "active_state_mutated": False,
            "runtime_history_appended": False,
            "quota_spent": False,
        },
        "write_policy": {
            "advisory": True,
            "append_runtime_history": False,
            "mutate_active_state": False,
            "grant_agent_command": False,
            "spend_quota": False,
        },
    }


def classification_for_dreaming_decision(decision: str) -> str:
    if decision == "approve":
        return "dreaming_proposal_approved"
    if decision == "defer":
        return "dreaming_proposal_deferred"
    if decision == "reject":
        return "dreaming_proposal_rejected"
    raise ValueError(f"decision must be one of: {', '.join(sorted(DREAMING_PROPOSAL_DECISIONS))}")


def _recommended_action_for_decision(decision: str, promoted_todo_id: str | None) -> str:
    if decision == "approve":
        if promoted_todo_id:
            return (
                f"Approved dreaming proposal was promoted into Agent Todo {promoted_todo_id}; "
                "run quota should-run before executing it."
            )
        return (
            "Approved dreaming proposal matched an existing Agent Todo; run quota "
            "should-run before executing the promoted work."
        )
    if decision == "defer":
        return "Deferred dreaming proposal; no delivery todo was created and no quota was spent."
    return "Rejected dreaming proposal; no delivery todo was created and no quota was spent."


def build_dreaming_decision_record(
    *,
    goal_id: str,
    registry_goal: dict[str, Any] | None,
    generated_at: str,
    classification: str,
    recommended_action: str,
    source_proposal: dict[str, Any],
    decision_payload: dict[str, Any],
) -> dict[str, Any]:
    adapter = (
        registry_goal.get("adapter")
        if isinstance(registry_goal, dict) and isinstance(registry_goal.get("adapter"), dict)
        else {}
    )
    promoted_todo_id = decision_payload.get("promoted_todo_id")
    health_check = (
        f"dreaming_decision decision={decision_payload.get('decision')}; "
        "source_proposal 1/1; "
        f"promoted_todo {1 if promoted_todo_id else 0}/1; "
        "quota_spent 0/1"
    )
    return {
        "generated_at": generated_at,
        "goal_id": goal_id,
        "classification": classification,
        "recommended_action": recommended_action,
        "health_check": health_check,
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "dreaming_decision": decision_payload,
        "source_dreaming_proposal": source_proposal,
        "registry_goal": {
            "present": bool(registry_goal),
            "domain": registry_goal.get("domain") if registry_goal else None,
            "status": registry_goal.get("status") if registry_goal else None,
            "adapter": {
                "kind": adapter.get("kind"),
                "status": adapter.get("status"),
            },
        },
    }


def record_dreaming_proposal_decision(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    proposal_id: str,
    decision: str,
    reason_summary: str,
    todo_text: str | None,
    claimed_by: str | None,
    dry_run: bool,
    sync_global: bool = True,
) -> dict[str, Any]:
    safe_goal_id = validate_goal_id_path_segment(goal_id)
    validate_public_safe_text("proposal_id", proposal_id)
    validate_public_safe_text("reason_summary", reason_summary)
    validate_public_safe_text("todo_text", todo_text)
    if decision not in DREAMING_PROPOSAL_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(DREAMING_PROPOSAL_DECISIONS))}")
    if decision == "approve" and not todo_text:
        raise ValueError("--todo-text is required when --decision approve")
    if decision != "approve" and todo_text:
        raise ValueError("--todo-text is only valid when --decision approve")

    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override)
    projection_route, compact_projection_route = prepare_material_projection_route(
        registry_path=registry_path,
        goal_id=safe_goal_id,
        source_runtime_root=runtime_root,
        sync_global=sync_global,
    )
    history_payload = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=safe_goal_id,
        limit=50,
        include_runtime_goals=False,
    )
    source_run = _find_source_proposal(history_payload, goal_id=safe_goal_id, proposal_id=proposal_id)
    if not source_run:
        raise ValueError(f"dreaming proposal not found for goal_id={safe_goal_id} proposal_id={proposal_id}")

    source_proposal = _compact_source_proposal(source_run)
    promoted_todo_id: str | None = None
    todo_result: dict[str, Any] | None = None
    todo_evidence_result: dict[str, Any] | None = None
    active_state_mutated = False
    if decision == "approve":
        if not dry_run:
            todo_result = add_goal_todo(
                registry_path=registry_path,
                goal_id=safe_goal_id,
                role="agent",
                text=str(todo_text or ""),
                task_class="advancement_task",
                action_kind="dreaming_proposal_promotion",
                claimed_by=claimed_by,
                dry_run=False,
            )
            promoted_todo_id = str(todo_result.get("todo_id") or "") or None
            registered_agents = registered_agent_ids_from_registry(
                registry_path,
                safe_goal_id,
            )
            if promoted_todo_id and (claimed_by or len(registered_agents) <= 1):
                todo_evidence_result = update_goal_todo(
                    registry_path=registry_path,
                    goal_id=safe_goal_id,
                    role="agent",
                    todo_id=promoted_todo_id,
                    evidence=f"dreaming_proposal:{proposal_id}",
                    note="Approved dreaming proposal promoted to normal Agent Todo.",
                    agent_id=claimed_by,
                    dry_run=False,
                )
            active_state_mutated = bool(
                todo_result
                and (todo_result.get("added") or todo_result.get("metadata_updated"))
                or todo_evidence_result
                and todo_evidence_result.get("changed")
            )

    classification = classification_for_dreaming_decision(decision)
    generated_at = now_local()
    decision_payload = {
        "schema_version": DREAMING_PROPOSAL_DECISION_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "decision": decision,
        "reason_summary": public_safe_compact_text(reason_summary, limit=260),
        "promoted_to_delivery": decision == "approve",
        "promoted_todo_id": promoted_todo_id,
        "created_todo_id": promoted_todo_id if todo_result and todo_result.get("added") else None,
        "todo_added": bool(todo_result and todo_result.get("added")),
        "delivery_spend_allowed": False,
        "quota_spent": False,
    }
    if claimed_by and decision == "approve":
        decision_payload["claimed_by"] = claimed_by
    recommended_action = _recommended_action_for_decision(decision, promoted_todo_id)
    validate_local_control_text("recommended_action", recommended_action)
    registry_goal = _registry_goal(registry, safe_goal_id)
    record = build_dreaming_decision_record(
        goal_id=safe_goal_id,
        registry_goal=registry_goal,
        generated_at=generated_at,
        classification=classification,
        recommended_action=recommended_action,
        source_proposal=source_proposal,
        decision_payload=decision_payload,
    )
    record["runtime_projection_route"] = compact_projection_route

    runs_dir = runtime_root / "goals" / safe_goal_id / "runs"
    index_record = {
        "generated_at": generated_at,
        "goal_id": safe_goal_id,
        "classification": classification,
        "recommended_action": recommended_action,
        "health_check": record["health_check"],
        "dreaming_decision": decision_payload,
        "source_dreaming_proposal": source_proposal,
        "runtime_projection_route": compact_projection_route,
    }
    runtime_history_appended = not dry_run
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": DREAMING_PROPOSAL_DECISION_SCHEMA_VERSION,
        "dry_run": dry_run,
        "appended": not dry_run,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_id": safe_goal_id,
        "proposal_id": proposal_id,
        "classification": classification,
        "recommended_action": recommended_action,
        "generated_at": generated_at,
        "health_check": record["health_check"],
        "decision": decision,
        "dreaming_decision": decision_payload,
        "source_dreaming_proposal": source_proposal,
        "runtime_projection_route": compact_projection_route,
        "todo_result": todo_result,
        "todo_evidence_result": todo_evidence_result,
        "side_effects": {
            "project_files_mutated": bool(runtime_history_appended or active_state_mutated),
            "active_state_mutated": bool(active_state_mutated),
            "runtime_history_appended": runtime_history_appended,
            "todo_added": bool(todo_result and todo_result.get("added")),
            "quota_spent": False,
        },
        "write_policy": {
            "append_runtime_history": True,
            "mutate_active_state": decision == "approve",
            "grant_agent_command": False,
            "spend_quota": False,
        },
    }
    if dry_run:
        payload.update(
            {
                "json_path": None,
                "markdown_path": None,
                "index_path": str(runs_dir / "index.jsonl"),
            }
        )
    else:
        write_reserved_run_artifacts(
            runs_dir=runs_dir,
            generated_at=generated_at,
            record=record,
            index_record=index_record,
            payload=payload,
            render_markdown=render_dreaming_decision_markdown,
        )
    projection_result = finalize_material_projection(
        registry_path=registry_path,
        source_runtime_root=runtime_root,
        goal_id=safe_goal_id,
        source_row=index_record,
        projection_kind="dreaming_decision",
        route=projection_route,
        sync_global=sync_global,
        dry_run=dry_run,
    )
    payload["global_sync"] = projection_result["global_sync"]
    payload["shared_runtime_material_projection"] = projection_result[
        "shared_runtime_material_projection"
    ]
    if not projection_result["ok"]:
        payload["ok"] = False
        payload["partial_write"] = projection_result["partial_write"]
    return payload


def render_dreaming_dry_run_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Dreaming Dry-Run Proposal",
        "",
        f"- Goal: `{payload.get('goal_id')}`",
        f"- OK: `{payload.get('ok')}`",
        f"- Dry run: `{payload.get('dry_run')}`",
    ]
    if payload.get("error"):
        lines.append(f"- Error: {payload.get('error')}")
        return "\n".join(lines) + "\n"

    side_effects = payload.get("side_effects") if isinstance(payload.get("side_effects"), dict) else {}
    contract = (
        payload.get("server_planning_contract")
        if isinstance(payload.get("server_planning_contract"), dict)
        else {}
    )
    lines.extend(
        [
            f"- Classification: `{payload.get('classification')}`",
            f"- Proposal id: `{payload.get('proposal_id')}`",
            f"- Proposal type: `{payload.get('proposal_type')}`",
            f"- Summary: {payload.get('summary')}",
            f"- Operator question: {payload.get('operator_question')}",
            f"- Runtime history appended: `{side_effects.get('runtime_history_appended')}`",
            f"- Active state mutated: `{side_effects.get('active_state_mutated')}`",
            f"- Quota spent: `{side_effects.get('quota_spent')}`",
        ]
    )
    if contract:
        lines.extend(
            [
                f"- Planning authority: `{contract.get('authority')}`",
                f"- May rank todos: `{contract.get('may_rank_candidate_todos')}`",
                f"- May execute protected actions: `{contract.get('may_execute_protected_actions')}`",
                f"- May read private material: `{contract.get('may_read_private_material')}`",
                f"- May spend delivery quota: `{contract.get('may_spend_delivery_quota')}`",
            ]
        )
    lines.extend(["", "## Recent Evidence", ""])
    evidence = payload.get("recent_evidence") if isinstance(payload.get("recent_evidence"), list) else []
    if not evidence:
        lines.append("- No recent non-neutral run evidence.")
    for item in evidence:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"`{item.get('classification')}` "
            f"{item.get('generated_at') or ''}: "
            f"{item.get('recommended_action') or item.get('delivery_outcome') or ''}"
        )
    return "\n".join(lines) + "\n"


def render_dreaming_decision_markdown(payload: dict[str, Any]) -> str:
    decision = (
        payload.get("dreaming_decision")
        if isinstance(payload.get("dreaming_decision"), dict)
        else {}
    )
    source = (
        payload.get("source_dreaming_proposal")
        if isinstance(payload.get("source_dreaming_proposal"), dict)
        else {}
    )
    side_effects = payload.get("side_effects") if isinstance(payload.get("side_effects"), dict) else {}
    lines = [
        "# Dreaming Proposal Decision",
        "",
        f"- Goal: `{payload.get('goal_id')}`",
        f"- OK: `{payload.get('ok')}`",
        f"- Dry run: `{payload.get('dry_run')}`",
        f"- Appended: `{payload.get('appended')}`",
        f"- Classification: `{payload.get('classification')}`",
        f"- Proposal id: `{payload.get('proposal_id')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Promoted to delivery: `{decision.get('promoted_to_delivery')}`",
        f"- Promoted todo: `{decision.get('promoted_todo_id')}`",
        f"- Created todo: `{decision.get('created_todo_id')}`",
        f"- Runtime history appended: `{side_effects.get('runtime_history_appended')}`",
        f"- Active state mutated: `{side_effects.get('active_state_mutated')}`",
        f"- Quota spent: `{side_effects.get('quota_spent')}`",
        f"- Recommended action: {payload.get('recommended_action')}",
        "",
        "## Source Proposal",
        "",
        f"- Generated at: `{source.get('generated_at')}`",
        f"- Classification: `{source.get('classification')}`",
        f"- Proposal type: `{source.get('proposal_type')}`",
        f"- Evidence window: `{source.get('evidence_window')}`",
    ]
    if source.get("summary"):
        lines.append(f"- Summary: {source.get('summary')}")
    return "\n".join(lines) + "\n"


def render_dreaming_markdown(payload: dict[str, Any]) -> str:
    if payload.get("schema_version") == DREAMING_PROPOSAL_DECISION_SCHEMA_VERSION:
        return render_dreaming_decision_markdown(payload)
    return render_dreaming_dry_run_markdown(payload)
