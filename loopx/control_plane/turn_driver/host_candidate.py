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
from .driver import selected_turn_todo

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
    unavailable = _mapping(_mapping(envelope.get("contract_capsule")).get("unavailable_context"))
    if unavailable:
        authority["unavailable_context"] = unavailable
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
    return (
        "You are executing one bounded LoopX-governed work segment.\n"
        "The JSON below is the complete host authority for this Turn. Execute "
        "primary_action only after reading work_context and any remaining required_reads. "
        "Do not repeat reads already fulfilled in work_context. Write only inside write_scope, "
        "and obey workspace_guard. Do not infer authority from other prose.\n\n"
        f"{context_instruction}"
        f"Turn authority JSON:\n{authority_json}\n\n"
        "When finished, return only one JSON object (no Markdown fence) with "
        "these public-safe fields:\n"
        "- result_kind: one of validated_progress | repair_required | "
        "replan_required | user_action_required | wait | iteration_failed\n"
        "- classification: short label (<=120 chars)\n"
        "- summary: what changed or why stopped (<=400 chars)\n"
        "- recommended_action: the bounded follow-up recommendation (<=1200 chars)\n"
        "- next_action: the concrete next step (<=1200 chars)\n"
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


def _complete_material_fields(result: dict[str, Any], kind: str) -> None:
    """Fill the required delivery fields of a material host candidate."""
    result["delivery_batch_scale"] = "single_surface"
    result["delivery_outcome"] = "outcome_progress"
    # Material results require these bounded text fields; fill them from
    # adjacent fields if the model returned a sparse block.
    if not result.get("recommended_action"):
        result["recommended_action"] = _bounded(
            result.get("next_action") or result.get("classification") or kind,
            limit=TEXT_LIMITS["recommended_action"],
        )
    if not result.get("next_action"):
        result["next_action"] = _bounded(
            result.get("recommended_action"),
            limit=TEXT_LIMITS["next_action"],
        )
    if not result.get("classification"):
        result["classification"] = _bounded(
            kind, limit=TEXT_LIMITS["classification"]
        )


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
    result: dict[str, Any] = {
        "schema_version": LOOPX_TURN_RESULT_SCHEMA,
        "turn_key": turn_key,
        "result_kind": kind,
        "completed_phases": list(COMPLETED_PHASES),
    }
    for field, limit in TEXT_LIMITS.items():
        if field == "vision_unchanged_reason":
            continue
        value = candidate.get(field)
        text = _bounded(value, limit=limit)
        if text:
            result[field] = text

    if kind in MATERIAL_KINDS:
        _complete_material_fields(result, kind)
    for field in ("path_delta_mode", "agent_vision_json"):
        if field in candidate:
            # Preserve the structured executor input verbatim. The executor
            # owns type, size, JSON and semantic validation.
            result[field] = candidate[field]

    has_path_delta = "path_delta_mode" in candidate or "agent_vision_json" in candidate
    unchanged_reason = candidate.get("vision_unchanged_reason")
    if unchanged_reason:
        result["vision_unchanged_reason"] = _bounded(
            unchanged_reason, limit=TEXT_LIMITS["vision_unchanged_reason"]
        )
    elif not has_path_delta:
        # Keep the legacy material-without-packet behavior for older hosts.
        result["vision_unchanged_reason"] = _bounded(
            "host reported material work without a goal vision replan packet"
            if kind in MATERIAL_KINDS
            else "host reported no material change",
            limit=TEXT_LIMITS["vision_unchanged_reason"],
        )
    return result
