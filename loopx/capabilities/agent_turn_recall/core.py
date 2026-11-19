from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...control_plane.digest_envelope import sha256_envelope
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ..context_providers.base import ContextProvider


AGENT_TURN_SITUATION_SCHEMA_VERSION = "agent_turn_situation_v0"
AGENT_TURN_RECALL_CONTEXT_SCHEMA_VERSION = "agent_turn_recall_context_v0"
AGENT_TURN_RECALL_SCHEMA_VERSION = "agent_turn_recall_v0"
AGENT_TURN_RECALL_SURFACE_ID = "agent_workflow.turn_admission"
MAX_RECENT_OUTCOMES = 3


def _compact(value: object, *, limit: int) -> str | None:
    return public_safe_compact_text(value, limit=limit) or None


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_envelope(encoded)


def _recent_outcomes(quota_decision: Mapping[str, Any]) -> list[str]:
    candidates: list[object] = []
    latest_run = _mapping(quota_decision.get("latest_run"))
    candidates.extend(
        [
            latest_run.get("classification"),
            quota_decision.get("latest_run_recommended_action"),
        ]
    )
    handoff = _mapping(quota_decision.get("handoff_readiness"))
    candidates.append(
        _mapping(handoff.get("post_handoff_latest_run")).get("classification")
    )
    result: list[str] = []
    for value in candidates:
        text = _compact(value, limit=180)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= MAX_RECENT_OUTCOMES:
            break
    return result


def _selected_todo(quota_decision: Mapping[str, Any]) -> dict[str, Any]:
    selected = _mapping(quota_decision.get("selected_todo"))
    compact: dict[str, Any] = {}
    for key in (
        "todo_id",
        "priority",
        "task_class",
        "action_kind",
        "target_key",
        "task_repository",
        "claimed_by",
        "next_due_at",
        "expires_at",
    ):
        if selected.get(key) is not None:
            compact[key] = selected[key]
    text = _compact(selected.get("text"), limit=320)
    if text:
        compact["text"] = text
    if not compact:
        fallback = _compact(quota_decision.get("recommended_action"), limit=320)
        if fallback:
            compact["text"] = fallback
    return compact


def build_agent_turn_situation(
    quota_decision: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    workspace_ref: str,
    project_ref: str,
    user_ref: str | None = None,
    session_ref: str | None = None,
) -> dict[str, Any]:
    """Build a bounded prompt-independent situation for one autonomous turn."""

    selected = _selected_todo(quota_decision)
    status_health_ok = quota_decision.get("status_health_ok") is True
    conflict_state = (
        "clear"
        if status_health_ok
        and not quota_decision.get("state_projection_gap")
        and not quota_decision.get("decision_freshness_warning")
        else "unresolved"
    )
    situation: dict[str, Any] = {
        "schema_version": AGENT_TURN_SITUATION_SCHEMA_VERSION,
        "goal_id": str(goal_id).strip(),
        "agent_id": str(agent_id).strip(),
        "turn_instance_id": str(turn_instance_id).strip(),
        "workspace_ref": str(workspace_ref).strip(),
        "project_ref": str(project_ref).strip(),
        "phase": _compact(
            quota_decision.get("lifecycle_phase")
            or quota_decision.get("effective_action")
            or quota_decision.get("state"),
            limit=120,
        ),
        "decision": _compact(quota_decision.get("decision"), limit=80),
        "selected_todo": selected,
        "recent_outcomes": _recent_outcomes(quota_decision),
        "next_intent": _compact(quota_decision.get("recommended_action"), limit=320),
        "conflict_state": conflict_state,
        "status_health_ok": status_health_ok,
        "user_prompt_included": False,
    }
    if user_ref:
        situation["user_ref"] = str(user_ref).strip()
    if session_ref:
        situation["session_ref"] = str(session_ref).strip()
    fingerprint_input = {
        key: value
        for key, value in situation.items()
        if key not in {"turn_instance_id"}
    }
    situation["situation_fingerprint"] = _canonical_hash(fingerprint_input)
    situation["turn_recall_id"] = _canonical_hash(
        {
            "turn_instance_id": situation["turn_instance_id"],
            "situation_fingerprint": situation["situation_fingerprint"],
        }
    )
    return situation


def _turn_query(situation: Mapping[str, Any]) -> dict[str, str]:
    selected = _mapping(situation.get("selected_todo"))
    fields = [
        f"agent={situation.get('agent_id')}",
        f"goal={situation.get('goal_id')}",
        f"project={situation.get('project_ref')}",
        f"phase={situation.get('phase') or 'unknown'}",
        f"todo={selected.get('text') or selected.get('todo_id') or 'none'}",
    ]
    for key in ("action_kind", "target_key"):
        if selected.get(key):
            fields.append(f"{key}={selected[key]}")
    if situation.get("next_intent"):
        fields.append(f"intent={situation['next_intent']}")
    outcomes = situation.get("recent_outcomes")
    if isinstance(outcomes, Sequence) and not isinstance(outcomes, (str, bytes)):
        if outcomes:
            fields.append("recent=" + "; ".join(map(str, outcomes)))
    query = _compact(" | ".join(fields), limit=500) or "autonomous agent turn"
    todo_ref = selected.get("todo_id") or selected.get("action_kind") or "unselected"
    summary = _compact(
        f"turn admission for {situation.get('goal_id')} {todo_ref} "
        f"during {situation.get('phase') or 'unknown'}",
        limit=220,
    )
    return {"query": query, "query_summary": summary or "agent turn admission"}


def build_agent_turn_recall_preview(
    situation: Mapping[str, Any],
) -> dict[str, Any]:
    query = _turn_query(situation)
    return {
        "ok": True,
        "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
        "status": "preview",
        "surface_id": AGENT_TURN_RECALL_SURFACE_ID,
        "turn_recall_id": situation.get("turn_recall_id"),
        "situation_fingerprint": situation.get("situation_fingerprint"),
        "context": {
            "schema_version": AGENT_TURN_RECALL_CONTEXT_SCHEMA_VERSION,
            "situation": dict(situation),
            "guidance": [],
        },
        "query_evidence": {
            "query_digest": _canonical_hash(query["query"]),
            "query_summary": query["query_summary"],
            "exact_query_exposed": False,
        },
        "provider_call_count": 0,
        "execute_required_for_recall": True,
        "grants_new_action_authority": False,
        "quota_spend_performed": False,
        "external_writes_performed": False,
        "suppress_external_sinks": True,
    }


def run_agent_turn_recall(
    config: Mapping[str, Any],
    situation: Mapping[str, Any],
    *,
    observed_at: str,
    read_authority_checkpoints: Mapping[str, Mapping[str, Any]],
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Recall guidance and inject it into this turn's private reasoning context."""

    from ..reward_memory.runtime_hooks import run_reward_memory_automatic_recall_hook

    query = _turn_query(situation)
    context = {
        "schema_version": AGENT_TURN_RECALL_CONTEXT_SCHEMA_VERSION,
        "situation": dict(situation),
        "guidance": [],
    }

    def apply_guidance(
        base_output: Any,
        items: tuple[Any, ...],
    ) -> Mapping[str, Any]:
        guidance = [
            {
                "candidate_ref": item.candidate_ref,
                "target_class": item.target_class,
                "content_summary": item.content_summary,
                "experience": (
                    dict(item.experience) if item.experience is not None else None
                ),
            }
            for item in items
        ]
        return {
            "outcome": "applied",
            "output": dict(base_output) | {"guidance": guidance},
            "memory_refs": [item.memory_ref for item in items],
            "reasoning_summary": (
                "recalled guidance was injected into the exact private turn context; "
                "the agent still decides applicability and receives no new authority"
            ),
            "current_artifact_verified": situation.get("conflict_state") == "clear",
        }

    result = run_reward_memory_automatic_recall_hook(
        config,
        surface_id=AGENT_TURN_RECALL_SURFACE_ID,
        base_output=context,
        workspace_ref=str(situation.get("workspace_ref") or ""),
        project_ref=str(situation.get("project_ref") or ""),
        user_ref=str(situation.get("user_ref") or "") or None,
        peer_ref=f"agent:{situation.get('agent_id')}",
        session_ref=str(situation.get("session_ref") or "") or None,
        revision_ref=str(situation.get("situation_fingerprint") or ""),
        queries=[query],
        observed_at=observed_at,
        freshness_context={
            "source_truth_current": situation.get("status_health_ok") is True,
            "source_revision": str(situation.get("situation_fingerprint") or ""),
            "age_seconds": 0,
        },
        conflict_state=str(situation.get("conflict_state") or "unresolved"),
        read_authority_checkpoints=read_authority_checkpoints,
        application_id=(
            "turn-recall-"
            + str(situation.get("turn_recall_id") or "").removeprefix("sha256:")[:20]
        ),
        artifact_ref=str(situation.get("situation_fingerprint") or ""),
        apply_memory=apply_guidance,
        provider=provider,
    )
    telemetry = _mapping(result.get("telemetry"))
    return {
        "ok": result.get("ok") is True,
        "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
        "status": result.get("status"),
        "reason_code": result.get("reason_code"),
        "surface_id": AGENT_TURN_RECALL_SURFACE_ID,
        "turn_recall_id": situation.get("turn_recall_id"),
        "situation_fingerprint": situation.get("situation_fingerprint"),
        "context": result.get("output", context),
        "application": result.get("application"),
        "recall_attempts": result.get("recall_attempts", []),
        "telemetry": telemetry,
        "provider_call_count": int(telemetry.get("provider_call_count") or 0),
        "fail_open": True,
        "fail_open_preserved_base": result.get("fail_open_preserved_base"),
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
        "quota_spend_performed": False,
        "external_writes_performed": False,
        "raw_provider_payload_captured": False,
        "suppress_external_sinks": True,
    }
