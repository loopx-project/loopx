"""Shared signed request and candidate conversion for governed Turn adapters.

Adapters execute work; this conversion does not grant authority, validate the
artifact, write canonical state or spend quota. The Turn executor owns those
boundaries. DSH and optional cloud hosts share this exact result contract.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any

from ..quota.turn_envelope import turn_envelope_action_signature_document
from ..work_items.delivery_batch_scale import DELIVERY_BATCH_SCALE_CHOICES
from ..work_items.delivery_outcome import DELIVERY_OUTCOME_CHOICES
from .driver import selected_turn_todo
from .executor import (
    HOST_RESULT_EVIDENCE_LIMITS,
    reward_memory_automation_enabled,
    reward_memory_prompt_instructions,
)

LOOPX_TURN_HOST_REQUEST_SCHEMA = "loopx_turn_host_request_v0"
LOOPX_TURN_RESULT_SCHEMA = "loopx_turn_result_v0"
COMPLETED_PHASES = ["host_execute", "typed_result"]

ACCEPTED_RESULT_KINDS = {
    "validated_progress",
    "repair_required",
    "replan_required",
    "user_action_required",
    "wait",
    "iteration_failed",
}
MATERIAL_KINDS = {"validated_progress", "repair_required", "replan_required"}

TEXT_LIMITS = {
    "classification": 120,
    "recommended_action": 1_200,
    "next_action": 1_200,
    "vision_unchanged_reason": 240,
    "summary": 400,
}

def _bounded(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _execution_profile_present(request: Mapping[str, Any]) -> bool:
    envelope = _mapping(request.get("turn_envelope"))
    capsule = _mapping(envelope.get("contract_capsule"))
    profile = capsule.get("execution_profile")
    return isinstance(profile, Mapping) and bool(profile)


def extract_turn_authority(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return the signed action and safety boundary exactly as projected."""

    envelope = _mapping(request.get("turn_envelope"))
    signature = _mapping(envelope.get("action_signature"))
    source_hash = str(signature.get("source_hash") or "")
    envelope_hash = str(signature.get("envelope_hash") or "")
    computed_envelope_hash = _canonical_hash(
        turn_envelope_action_signature_document(envelope)
    )
    if (
        signature.get("matches") is not True
        or not source_hash
        or source_hash != envelope_hash
        or envelope_hash != computed_envelope_hash
    ):
        raise ValueError("TurnEnvelope action signature is missing or does not match")

    action = _mapping(envelope.get("action"))
    primary_action = action.get("primary_action")
    if not isinstance(primary_action, str) or not primary_action.strip():
        raise ValueError("signed TurnEnvelope has no primary_action")

    boundary = _mapping(envelope.get("boundary"))
    required_reads = envelope.get("required_reads")
    write_scope = boundary.get("write_scope")
    authority = {
        "primary_action": primary_action,
        "required_reads": list(required_reads) if isinstance(required_reads, list) else [],
        "write_scope": list(write_scope) if isinstance(write_scope, list) else [],
        "workspace_guard": _mapping(boundary.get("workspace_guard")),
    }
    # Keep the established scope/workspace aliases once, and transport every
    # remaining signed restriction rather than rebuilding policy in this IO adapter.
    remaining_boundary = {
        key: value for key, value in boundary.items()
        if key not in {"write_scope", "workspace_guard"}
    }
    if remaining_boundary:
        authority["boundary"] = remaining_boundary
    if isinstance(envelope.get("work_context"), Mapping):
        authority["work_context"] = dict(envelope["work_context"])
    selected = selected_turn_todo(envelope)
    if selected:
        # Both the declaration and a possible exact-text alias are signed.
        # Do not replace a complete task with the shorter primary-action label.
        if selected.get("text_ref") == "action.recommended_action":
            selected = {**selected, "text": action.get("recommended_action")}
            selected.pop("text_ref")
        authority["selected_todo"] = selected
    capsule = _mapping(envelope.get("contract_capsule"))
    for field in ("unavailable_context", "vision_continuation_audit", "execution_profile"):
        value = _mapping(capsule.get(field))
        if value:
            authority[field] = value
    context = _mapping(envelope.get("agent_context"))
    if context:
        authority["agent_context"] = context
    replan = _mapping(envelope.get("replan_action_packet"))
    if replan:
        authority["replan_action_packet"] = replan
    if context.get("detail_ref") or replan or authority.get("vision_continuation_audit"):
        # Replan/acceptance evidence and historical capability references need
        # the saved observation's route and hash. Native capture validation owns
        # its binding; this adapter neither reads it nor re-enters admission.
        authority["detail_ref"] = _mapping(envelope.get("detail_ref"))
        authority["action_signature"] = signature
    recall_enabled = reward_memory_automation_enabled(request, operation="automatic_recall")
    ingest_enabled = reward_memory_automation_enabled(request, operation="automatic_ingest")
    if recall_enabled or ingest_enabled:
        memory = _mapping(request.get("reward_memory_recall"))
        if not recall_enabled:
            memory["context"] = None
        authority["reward_memory_recall"] = memory
        authority["reward_memory_instructions"] = reward_memory_prompt_instructions(request)
    return authority


def extract_action_text(request: Mapping[str, Any]) -> str:
    """Return the bounded, control-plane-authored task body for the host."""

    return str(extract_turn_authority(request)["primary_action"])


def render_prompt(authority: Mapping[str, Any]) -> str:
    """Wrap one signed Turn authority packet in a typed JSON result request.

    The host owns execution. The final assistant message is the only channel this
    adapter reads back as a typed candidate; it stays public-safe and bounded.
    """

    authority_json = json.dumps(
        dict(authority),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    context_instruction = (
        "For unavailable_context, discard cached context from the affected hooks; "
        "do not interpret failure as empty context. Hold actions that require that "
        "missing context until it is read successfully. Independent work may "
        "continue under its existing authority; this grants no new permissions.\n"
        if authority.get("unavailable_context") else ""
    )
    planning_context_instruction = (
        "Read agent_context before planning; its guidance and facts grant no permissions. "
        "Only an agent_context.detail_ref requires capability detail: use detail_ref.full_decision "
        "only for the saved original observation identified by detail_ref.captured_decision, "
        "checking its Goal/Agent/Turn and source hash. Never re-enter quota admission for a detail read. "
        "Missing or invalid original evidence holds dependent planning until recovery; "
        "independent work retains its existing authority.\n"
        if authority.get("agent_context") else ""
    )
    replan_instruction = (
        "Use replan_action_packet to choose one permitted semantic outcome. "
        "Before choosing it, read autonomous_replan_obligation.replan_context "
        "through detail_ref.full_decision from the saved original observation "
        "identified by detail_ref.captured_decision; check Goal/Agent/Turn and "
        "action_signature.source_decision_hash. Never re-enter quota admission "
        "for a detail read. Missing or invalid original evidence holds dependent "
        "replanning; independent work retains its existing authority. "
        "Do not execute its writeback commands; the outer Turn executor owns "
        "state, quota and scheduler effects. Guidance grants no permissions.\n"
        if authority.get("replan_action_packet") else ""
    )
    acceptance_instruction = (
        "Read vision_continuation_audit before judging Goal acceptance or proposing "
        "closeout; preserve its ordered required_before_closeout, evidence requirements "
        "and current vision_gap_judge. Selected Todo completion alone cannot close the Goal. "
        "For additional acceptance or coverage evidence, use detail_ref.full_decision "
        "only for the saved original observation identified by detail_ref.captured_decision; "
        "check its Goal/Agent/Turn and action_signature.source_decision_hash. "
        "Never re-enter quota admission for a detail read. Missing or invalid evidence "
        "holds dependent closure; independent work retains its existing authority. "
        "Do not execute its state or scheduler commands; report the semantic result "
        "for the outer Turn executor. The audit grants no new authority.\n"
        if authority.get("vision_continuation_audit") else ""
    )
    work_instruction = (
        "Use execution_profile to size and qualify the work, including its required "
        "artifact, validation, outcome and degradation rules. Return the work and "
        "validation evidence; state writeback and quota effects belong to the outer "
        "Turn executor. The profile grants no new permissions.\n"
        if authority.get("execution_profile") else ""
    )
    return (
        "You are executing one bounded LoopX-governed work segment.\n"
        "The JSON below is the complete host authority for this Turn. Execute "
        "primary_action only after reading work_context and any remaining required_reads. "
        "Do not repeat reads already fulfilled in work_context. Write only inside write_scope, "
        "and obey workspace_guard and all supplied boundary restrictions and stop conditions. "
        "Checkpointed approval covers only its active_write_scope; other approval gates remain. "
        "Do not infer authority from other prose.\n\n"
        f"{context_instruction}"
        f"{planning_context_instruction}"
        f"{replan_instruction}"
        f"{acceptance_instruction}"
        f"{work_instruction}"
        f"Turn authority JSON:\n{authority_json}\n\n"
        "When finished, return only one JSON object (no Markdown fence) with "
        "these public-safe fields:\n"
        "- result_kind: one of validated_progress | repair_required | "
        "replan_required | user_action_required | wait | iteration_failed\n"
        "- classification: short label (<=120 chars)\n"
        "- summary: what changed or why stopped (<=400 chars)\n"
        "- recommended_action: the bounded follow-up recommendation (<=1200 chars)\n"
        "- next_action: the concrete next step (<=1200 chars)\n"
        f"- delivery_batch_scale (material results): {' | '.join(DELIVERY_BATCH_SCALE_CHOICES)}\n"
        f"- delivery_outcome (material results): {' | '.join(DELIVERY_OUTCOME_CHOICES)}\n"
        "- vision_unchanged_reason: why a material result's goal path is unchanged "
        "(<=240 chars)\n"
        "For material result kinds (validated_progress, repair_required, and "
        "replan_required), describe the path outcome. For a material goal-path "
        "change, set path_delta_mode to material_replan, "
        "result_kind to replan_required, and agent_vision_json to a JSON-encoded "
        "bounded goal_vision_replan_contract_v0 packet whose path_delta is "
        "goal_path_delta_v0 with outcome replan; leave vision_unchanged_reason "
        "empty. For unchanged paths, set path_delta_mode to unchanged, omit "
        "agent_vision_json, and provide vision_unchanged_reason. These fields "
        "describe the result only; they grant no authority. For stop results "
        "(wait, user_action_required, iteration_failed), omit both "
        "path_delta_mode and agent_vision_json.\n"
        "Use repair_required when the task is sound but a recoverable defect "
        "blocks it, replan_required when this route is exhausted, and "
        "wait/user_action_required when no material write is safe, and "
        "iteration_failed when this iteration failed without authorizing a "
        "retry or successor. "
        "Do not include raw transcripts, credentials, or absolute local paths."
        + (
            " Because execution_profile is present, every material result must "
            "also return work_evidence and validation_evidence as separate arrays "
            "of 1-8 short public-safe strings (<=240 characters each, <=1200 "
            "characters total per field). Name the artifact or observation as work "
            "evidence and the command or readback that checked it as validation "
            "evidence. Omit both fields for wait, user_action_required and "
            "iteration_failed."
            if authority.get("execution_profile") else ""
        )
    )


def parse_model_json(text: str) -> dict[str, Any] | None:
    """Parse the host final assistant message as one JSON object.

    Prefer exact JSON; fall back to the outermost object so a model that wraps
    the result in prose or a code fence still produces a typed candidate.
    """

    value = text.strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Strip a Markdown code fence if present.
    lines = value.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    value = "\n".join(lines).strip()

    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(value[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _complete_material_fields(
    result: dict[str, Any], candidate: Mapping[str, Any], kind: str,
) -> None:
    """Fill the required delivery fields of a material host candidate."""
    # Only omitted metadata gets the legacy defaults. Supplied values, including
    # null and invalid JSON types, belong to the existing executor validators.
    result["delivery_batch_scale"] = candidate.get("delivery_batch_scale", "single_surface")
    result["delivery_outcome"] = candidate.get("delivery_outcome", "outcome_progress")
    # Preserve sparse legacy candidates; the executor validates both supplied
    # fields and these aliases against the same result contract.
    if "recommended_action" not in result:
        result["recommended_action"] = result.get("next_action") or result.get("classification") or kind
    if "next_action" not in result:
        result["next_action"] = result.get("recommended_action")
    if "classification" not in result:
        result["classification"] = kind


def build_result(
    request: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
    *,
    fallback_reason: str = "",
    host_name: str = "Host",
) -> dict[str, Any]:
    """Shape a host model result block into a valid loopx_turn_result_v0."""

    turn_key = str(request.get("turn_key") or "")
    if candidate is None:
        # Fail closed: no typed material claim means a stop, never fabricated
        # progress. This spends no quota.
        return {
            "schema_version": LOOPX_TURN_RESULT_SCHEMA,
            "turn_key": turn_key,
            "result_kind": "wait",
            "completed_phases": list(COMPLETED_PHASES),
            "classification": "no_typed_host_result",
            "next_action": _bounded(
                fallback_reason
                or f"{host_name} returned no typed JSON result; rerun or inspect the host session.",
                limit=TEXT_LIMITS["next_action"],
            ),
            "vision_unchanged_reason": _bounded(
                "host adapter could not confirm a material change",
                limit=TEXT_LIMITS["vision_unchanged_reason"],
            ),
        }

    kind = str(candidate.get("result_kind") or "").strip()
    if kind not in ACCEPTED_RESULT_KINDS:
        return {
            "schema_version": LOOPX_TURN_RESULT_SCHEMA,
            "turn_key": turn_key,
            "result_kind": "wait",
            "completed_phases": list(COMPLETED_PHASES),
            "classification": "unsupported_host_result_kind",
            "next_action": _bounded(
                fallback_reason
                or f"{host_name} returned unsupported result_kind "
                + repr(kind) + ".",
                limit=TEXT_LIMITS["next_action"],
            ),
            "vision_unchanged_reason": _bounded(
                "host adapter could not accept the returned result kind",
                limit=TEXT_LIMITS["vision_unchanged_reason"],
            ),
        }
    if kind in MATERIAL_KINDS and _execution_profile_present(request):
        missing_evidence = [
            field for field in HOST_RESULT_EVIDENCE_LIMITS
            if field not in candidate
        ]
        if missing_evidence:
            return {
                "schema_version": LOOPX_TURN_RESULT_SCHEMA,
                "turn_key": turn_key,
                "result_kind": "wait",
                "completed_phases": list(COMPLETED_PHASES),
                "classification": "missing_host_execution_evidence",
                "next_action": _bounded(
                    f"{host_name} returned a material result without required "
                    + " and ".join(missing_evidence)
                    + "; rerun with typed public-safe work and validation evidence.",
                    limit=TEXT_LIMITS["next_action"],
                ),
                "vision_unchanged_reason": _bounded(
                    "host adapter could not accept material work without typed evidence",
                    limit=TEXT_LIMITS["vision_unchanged_reason"],
                ),
            }
    result: dict[str, Any] = {
        "schema_version": LOOPX_TURN_RESULT_SCHEMA,
        "turn_key": turn_key,
        "result_kind": kind,
        "completed_phases": list(COMPLETED_PHASES),
    }
    for field in TEXT_LIMITS:
        if field == "vision_unchanged_reason":
            continue
        value = candidate.get(field)
        # The executor must see an over-limit candidate and reject it. Clipping
        # can remove a trailing constraint and turn invalid input into a valid
        # but semantically different next action or progress claim.
        text = value.strip() if isinstance(value, str) else value
        if text or (value is not None and not isinstance(value, str)):
            result[field] = text
    if kind in MATERIAL_KINDS:
        _complete_material_fields(result, candidate, kind)
    for field in ("path_delta_mode", "agent_vision_json"):
        if field in candidate:
            # Preserve the structured executor input verbatim. The executor
            # owns type, size, JSON and semantic validation.
            result[field] = candidate[field]
    if ("reward_memory_reflection_json" in candidate
            and reward_memory_automation_enabled(request, operation="automatic_ingest")):
        # The outer executor validates bounds, material outcome, exact digest
        # attestation and evidence. This adapter must not silently drop its input.
        result["reward_memory_reflection_json"] = candidate["reward_memory_reflection_json"]
    for field in HOST_RESULT_EVIDENCE_LIMITS:
        if field in candidate:
            # Preserve the bounded evidence value for the executor's typed
            # validation and durable host-result readback.
            result[field] = candidate[field]

    has_path_delta = "path_delta_mode" in candidate or "agent_vision_json" in candidate
    unchanged_reason = candidate.get("vision_unchanged_reason")
    if unchanged_reason is not None and not isinstance(unchanged_reason, str):
        result["vision_unchanged_reason"] = unchanged_reason
    elif unchanged_reason:
        result["vision_unchanged_reason"] = unchanged_reason.strip()
    elif not has_path_delta:
        # Keep the legacy material-without-packet behavior for older hosts.
        result["vision_unchanged_reason"] = _bounded(
            "host reported material work without a goal vision replan packet"
            if kind in MATERIAL_KINDS
            else "host reported no material change",
            limit=TEXT_LIMITS["vision_unchanged_reason"],
        )
    return result
