"""Compact transport for typed delivery diagnostics and pre-write validation."""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from ..effect_runtime import effect_runtime_result


def _text(value: Any) -> str:
    # Preserve Python's legacy scalar/Enum transport without classifying prose.
    return str((value.value if isinstance(value, Enum) else value) or "")


def _bounded_text(text: str) -> str:
    # A non-whitespace invalid suffix survives downstream trim/normalization.
    # A raw prefix could end with spaces and alias a valid enum or identifier.
    return text if len(text) <= 128 else text[:128] + "!"


def _identifier_fact(value: Any) -> str:
    text = _text(value)
    # Keep one excess character as invalidity evidence, not a truncated valid id.
    # Whitespace-only bindings stay present for the exactly-one-scope check.
    return _bounded_text(text.strip()) or (" " if text else "")


def _run_facts(run: Mapping[str, Any]) -> dict[str, Any]:
    observation = run.get("progress_observation")
    compact_observation: dict[str, Any] | None = None
    if isinstance(observation, Mapping):
        compact_observation = {
            **{key: _bounded_text(_text(observation.get(key))) for key in ("schema_version", "result_class")},
            **{key: _identifier_fact(observation.get(key)) for key in ("work_item_id", "blocker_id")},
        }
        evidence = observation.get("evidence_ids")
        compact_observation["evidence_ids"] = (
            [_identifier_fact(value) for value in evidence] if isinstance(evidence, list) else None
        )
    return {
        **{key: _bounded_text(_text(run.get(key)).strip()) for key in (
            "delivery_outcome", "delivery_batch_scale", "delivery_turn_kind",
        )},
        **{key: _identifier_fact(run.get(key)) for key in ("todo_id", "replan_obligation_id")},
        "outcome_followthrough_required": run.get("outcome_followthrough_required") is True,
        "progress_observation": compact_observation,
    }


def project_delivery_history(
    runs: list[dict[str, Any]], *, outcome_floor_configured: bool = False,
    settlement_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a caller-selected history batch with one managed-runtime request.

    Do not send raw trajectories, evidence bodies, recommendations or profiles.
    The profile adapter passes only whether the legacy outcome floor is enabled.
    """
    request: dict[str, Any] = {
        "schema_version": "delivery_history_request_v0",
        "runs": [{**_run_facts(run),
            **{key: _identifier_fact(run.get(key)) for key in (
                "goal_id", "agent_id", "turn_instance_id", "generated_at",
            )},
            "receipt_required": run.get("settlement_identity") is not None or run.get("vision_checkpoint") is not None,
            "effect_id": _text((run.get("settlement_identity") or {}).get("effect_id"))[:1024]
                if isinstance(run.get("settlement_identity"), Mapping) else "",
            "vision_request_digest": _bounded_text(_text(run["refresh_recovery"].get("vision_request_digest")))
                if isinstance(run.get("refresh_recovery"), Mapping) else "",
            **({"goal_ref": {key: _identifier_fact(run["goal_ref"].get(key))
                             for key in ("goal_id", "goal_instance_id")}
                if isinstance(run.get("goal_ref"), Mapping)
                    and set(run["goal_ref"]) == {"goal_id", "goal_instance_id"}
                    and all(isinstance(value, str) for value in run["goal_ref"].values()) else None}
               if "goal_ref" in run else {}),
        } for run in runs],
        "outcome_floor_configured": outcome_floor_configured,
    }
    if settlement_source is None or not any(row["receipt_required"] or row["turn_instance_id"] for row in request["runs"]):
        result = effect_runtime_result("work_item.delivery_history.project", request)
    else:
        from ..quota.accounting_admission import quota_accounting_admission

        source = {key: settlement_source[key] for key in ("runtime_root", "goal_id", "goal_ref")
                  if key in settlement_source}
        with quota_accounting_admission(
            runtime_root=Path(source["runtime_root"]), registry_path=settlement_source.get("registry_path"),
            goal_id=source["goal_id"], goal_ref=source.get("goal_ref"),
            operation="delivery-history-read", lock_legacy_index=False,
        ) as admission:
            if admission is not None:
                source["source_admission"] = admission
            result = effect_runtime_result("work_item.delivery_history.project", {**request, "settlement_source": source})
    if (not isinstance(result, dict) or result.get("schema_version") != "delivery_history_v0"
        or not isinstance(result.get("runs"), list) or len(result["runs"]) != len(runs)):
        raise RuntimeError("TypeScript delivery history shape mismatch")
    for run, signal in zip(runs, result["runs"], strict=True):
        hint = signal["outcome_followthrough"]
        if hint is not None:
            # Display-only annotation after the decision; narrative never enters TS.
            hint["latest_classification"] = _text(run.get("classification")).strip()
    return result


def require_consistent_delivery_claim(record: Mapping[str, Any]) -> None:
    """Reject contradictory authored claims before effects; add no new fields."""
    if not any(record.get(key) for key in ("delivery_outcome", "delivery_turn_kind", "outcome_followthrough_required")):
        return
    result = effect_runtime_result("work_item.delivery_claim.validate", _run_facts(record))
    if not isinstance(result, dict) or result.get("schema_version") != "delivery_claim_validation_v0":
        raise RuntimeError("TypeScript delivery claim validation shape mismatch")
    if result.get("valid") is not True:
        raise ValueError("contradictory delivery claim: " + ", ".join(result.get("conflicts") or []))


def project_delivery_response(
    run: Mapping[str, Any], summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Select a canonical source row; TS alone decides its supervision meaning."""
    from ..todos.summary_item import todo_planning_source_items
    from ..todos.todo_semantics import todo_summary_claim_scope_agent_id

    source = next((item for item in todo_planning_source_items(summary, include_terminal=True)
                   if item.get("todo_id") == run.get("todo_id")), None) if summary else None
    fields = ("todo_id", "role", "status", "task_class", "archive_state", "claimed_by",
              "excluded_agents", "resume_when", "resume_ready", "resume_condition",
              "resume_monitor_generation", "task_repository")
    todo = {key: source[key] for key in fields if key in source} if source else None
    if todo and isinstance(todo.get("resume_condition"), dict):
        condition = todo["resume_condition"]
        todo["resume_condition"] = {key: condition[key] for key in (
            "schema_version", "resume_when", "satisfied", "invalid_target", "invalid_state",
            "kind", "target", "target_todo_id", "target_status", "target_task_class", "target_archive_state",
            "baseline_generation", "material_change_generation", "monitor_change_scope",
            "scoped_material_change_generation", "provider_required", "provider", "capability",
            "pr_repo", "pr_number", "repository_binding_state", "repository_binding_source",
            "scheduled_for", "evaluated_at", "clock_provider", "generation_fence", "material_change",
            "resume_receipt",
        ) if key in condition}
    result = effect_runtime_result("work_item.delivery_response.project", {
        "run": _run_facts(run), "todo": todo,
        "agent_id": todo_summary_claim_scope_agent_id(summary),
        "run_agent_id": run.get("agent_id"),
    })
    if not isinstance(result, dict) or result.get("schema_version") != "delivery_response_v0":
        raise RuntimeError("TypeScript delivery response shape mismatch")
    if result["outcome_followthrough"] is not None:
        result["outcome_followthrough"]["latest_classification"] = _text(run.get("classification")).strip()
    return result


def compact_delivery_binding(run: Mapping[str, Any]) -> dict[str, Any]:
    """Retain compact evidence identity, never evidence bodies, for read decisions."""
    facts = _run_facts(run)
    return {key: facts[key] for key in ("todo_id", "replan_obligation_id", "progress_observation")
            if facts[key]} | ({"agent_id": _bounded_text(_text(run["agent_id"]))} if run.get("agent_id") else {})
