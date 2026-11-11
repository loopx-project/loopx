"""Bounded material rerank proposal and apply receipt contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from ...control_plane.effect_runtime import (
    EffectRuntimeRejected,
    EffectRuntimeStartupError,
    effect_runtime_result,
)
from .ownership import MaterialProjectScope, material_owner_fields

from ._validation import (
    APPLY_STATUSES,
    capability_contract,
    check_record_keys,
    compact_text,
    compact_token,
    iso_timestamp,
    nonnegative_int,
    packet_ref,
    positive_int,
    token_list,
)

MATERIAL_RERANK_PROPOSAL_SCHEMA_VERSION = "material_rerank_proposal_v0"
MATERIAL_RERANK_APPLY_RECEIPT_SCHEMA_VERSION = "material_rerank_apply_receipt_v0"
MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION = "material_wave_rerank_plan_v0"
MATERIAL_RERANK_RECEIPT_MAX_REFS = 100

_WAVE_ANNOTATION_FIELDS = {
    "blocks_active_deliverable",
    "decision_gain_score",
    "effort_band",
    "evidence_refs",
    "evidence_role",
    "horizon_wave",
    "material_ref",
    "primary_source_refs",
    "reason_code",
    "scoped_slice_ready",
}

_MOVE_FIELDS = {
    "evidence_refs",
    "from_rank",
    "material_ref",
    "reason_code",
    "to_rank",
}
_REQUIRED_MOVE_FIELDS = {
    "from_rank",
    "material_ref",
    "reason_code",
    "to_rank",
}
def _ordered_material_refs(values: Sequence[str], *, field: str) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of compact tokens")
    refs = [compact_token(value, field=f"{field}[]") for value in values]
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field} values must be unique")
    return refs


def plan_material_single_move(
    ordered_material_refs: Sequence[str],
    material_ref: str,
    to_rank: int,
    *,
    protected_material_refs: Sequence[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Preview one ranked material move, preserving all other relative order.

    The complete ranked set includes the backlog; a visible Top-N does not
    implicitly pin its entries. Explicit anchors retain their exact rank,
    including when the selected material would displace them. These bounds
    describe the affected interval, not permission to apply it. The caller
    still supplies verified inventory/Decision Context to the proposal builder
    and checks the exact preview, owner gate, revision and rollback on apply.
    """
    refs = _ordered_material_refs(ordered_material_refs, field="ordered_material_refs")
    if not refs:
        raise ValueError("ordered_material_refs must contain at least one item")
    selected = compact_token(material_ref, field="material_ref")
    if selected not in refs:
        raise ValueError(
            "ranked material identity required; use intake settlement for new entries"
        )
    if type(to_rank) is not int or not 1 <= to_rank <= len(refs):
        raise ValueError("target rank must be an integer within the ranked queue")
    protected = token_list(protected_material_refs, field="protected_material_refs")
    if not set(protected).issubset(refs):
        raise ValueError("protected_material_refs must belong to the ranked queue")
    start = refs.index(selected)
    target = to_rank - 1
    reordered = refs.copy()
    reordered.pop(start)
    reordered.insert(target, selected)
    affected = (
        set(refs[min(start, target) : max(start, target) + 1])
        if start != target
        else set()
    )
    if affected.intersection(protected):
        raise ValueError("single material move would change a protected rank")
    displacement = abs(start - target)
    return reordered, {
        "target_window_size": len(refs),
        "max_moved_items": displacement + 1 if displacement else 1,
        "max_rank_displacement": max(1, displacement),
        "protected_material_refs": protected,
    }


def _deduplicated_tokens_preserving_order(
    values: Sequence[Any] | None,
    *,
    field: str,
    max_items: int = 10_000,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of compact tokens")
    if len(values) > max_items:
        raise ValueError(f"{field} must contain at most {max_items} items")
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        token = compact_token(value, field=f"{field}[]")
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def _normalize_wave_annotations(
    annotations: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if annotations is None:
        return []
    if isinstance(annotations, (str, bytes)) or not isinstance(annotations, Sequence):
        raise TypeError("annotations must be a sequence of objects")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(annotations):
        field = f"annotations[{index}]"
        check_record_keys(
            raw,
            field=field,
            allowed=_WAVE_ANNOTATION_FIELDS,
            required={"material_ref"},
        )
        entry: dict[str, Any] = {
            "material_ref": compact_token(
                raw["material_ref"],
                field=f"{field}.material_ref",
            )
        }
        if raw.get("horizon_wave") is not None:
            entry["horizon_wave"] = compact_token(
                raw["horizon_wave"],
                field=f"{field}.horizon_wave",
            )
        if raw.get("effort_band") is not None:
            entry["effort_band"] = compact_token(
                raw["effort_band"],
                field=f"{field}.effort_band",
            )
        if raw.get("scoped_slice_ready") is not None:
            scoped_ready = raw["scoped_slice_ready"]
            if not isinstance(scoped_ready, bool):
                raise ValueError(f"{field}.scoped_slice_ready must be a boolean")
            entry["scoped_slice_ready"] = scoped_ready
        if raw.get("decision_gain_score") is not None:
            score = raw["decision_gain_score"]
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not 0.0 <= float(score) <= 100.0
            ):
                raise ValueError(
                    f"{field}.decision_gain_score must be a finite number between 0 and 100"
                )
            entry["decision_gain_score"] = float(score)
        if raw.get("evidence_role") is not None:
            entry["evidence_role"] = compact_token(
                raw["evidence_role"],
                field=f"{field}.evidence_role",
            )
        if raw.get("blocks_active_deliverable") is not None:
            blocks = raw["blocks_active_deliverable"]
            if not isinstance(blocks, bool):
                raise ValueError(f"{field}.blocks_active_deliverable must be a boolean")
            entry["blocks_active_deliverable"] = blocks
        if raw.get("primary_source_refs") is not None:
            entry["primary_source_refs"] = _deduplicated_tokens_preserving_order(
                raw["primary_source_refs"],
                field=f"{field}.primary_source_refs",
                max_items=20,
            )
        if raw.get("reason_code") is not None:
            entry["reason_code"] = compact_token(
                raw["reason_code"],
                field=f"{field}.reason_code",
            )
        if raw.get("evidence_refs") is not None:
            entry["evidence_refs"] = token_list(
                raw["evidence_refs"],
                field=f"{field}.evidence_refs",
                max_items=10,
            )
        normalized.append(entry)
    return normalized


def plan_material_wave_rerank(
    ordered_material_refs: Sequence[str],
    annotations: Sequence[Mapping[str, Any]] | None = None,
    *,
    top_window_size: int = 30,
    protected_material_refs: Sequence[str] | None = None,
    required_load_bearing_refs: Sequence[str] | None = None,
    max_orthogonal_in_top_window: int = 0,
    default_evidence_refs: Sequence[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Preview a deterministic multi-item wave rerank via the shared TS owner.

    Stratifies ranked items across horizon waves and effort bands, enforces
    primary-over-secondary lineage precedence and active-deliverable shortlist
    guarantees, caps orthogonal signals inside ``top_window_size``, preserves
    exact set membership, protected anchor positions, and untouched backlog
    relative order, and returns moves plus bounds ready for
    ``build_material_rerank_proposal``. Never authorizes or executes a write.
    """
    refs = _ordered_material_refs(ordered_material_refs, field="ordered_material_refs")
    if not refs:
        raise ValueError("ordered_material_refs must contain at least one item")
    window = positive_int(top_window_size, field="top_window_size")
    max_orthogonal = nonnegative_int(
        max_orthogonal_in_top_window,
        field="max_orthogonal_in_top_window",
    )
    protected = token_list(
        protected_material_refs,
        field="protected_material_refs",
    )
    required_primaries = _deduplicated_tokens_preserving_order(
        required_load_bearing_refs,
        field="required_load_bearing_refs",
    )
    default_evidence = token_list(
        default_evidence_refs,
        field="default_evidence_refs",
        max_items=10,
    )
    normalized_annotations = _normalize_wave_annotations(annotations)

    request_payload: dict[str, Any] = {
        "ordered_material_refs": refs,
        "annotations": normalized_annotations,
        "top_window_size": window,
        "protected_material_refs": protected,
        "required_load_bearing_refs": required_primaries,
        "max_orthogonal_in_top_window": max_orthogonal,
        "default_evidence_refs": default_evidence,
    }
    try:
        result = effect_runtime_result(
            "material_lifecycle.plan_wave_rerank",
            request_payload,
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    except EffectRuntimeStartupError as exc:
        raise ValueError(
            "material wave ranking runtime unavailable; run loopx doctor before retrying"
        ) from exc
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION
    ):
        raise TypeError("typed material wave rerank result mismatch")
    reordered = [str(ref) for ref in result["reordered_material_refs"]]
    summary: dict[str, Any] = {
        "schema_version": result["schema_version"],
        "top_window_size": result["top_window_size"],
        "max_orthogonal_in_top_window": result["max_orthogonal_in_top_window"],
        "no_change": result["no_change"],
        "constraints": dict(result["constraints"]),
        "moves": [dict(move) for move in result["moves"]],
        "wave_counts": dict(result["wave_counts"]),
        "top_window_orthogonal_count": result["top_window_orthogonal_count"],
        "demoted_monograph_refs": list(result["demoted_monograph_refs"]),
        "shifted_orthogonal_refs": list(result["shifted_orthogonal_refs"]),
        "lineage_promoted_refs": list(result["lineage_promoted_refs"]),
    }
    return reordered, summary


def material_rerank_receipt_chunks(
    applied_material_refs: Sequence[str],
) -> list[list[str]]:
    """Cover one transition without truncating its affected material refs.

    Each chunk fits the existing v0 receipt. Validate the complete input before
    returning any chunks; duplicates are errors, not silently deduplicated.
    Build all receipts before CAS, keep their proposal/revisions/gate identical,
    then persist all of them after readback. This function performs no apply.
    Empty input produces one empty chunk for no-change/rejection/rollback.
    """
    refs = _ordered_material_refs(applied_material_refs, field="applied_material_refs")
    return [
        refs[index : index + MATERIAL_RERANK_RECEIPT_MAX_REFS]
        for index in range(0, len(refs), MATERIAL_RERANK_RECEIPT_MAX_REFS)
    ] or [[]]


def _normalize_moves(
    values: Sequence[Mapping[str, Any]] | None,
    *,
    protected_material_refs: set[str],
    max_moved_items: int,
    max_rank_displacement: int,
    target_window_size: int,
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("moves must be a sequence of objects")
    if len(values) > max_moved_items:
        raise ValueError("moves exceeds max_moved_items")

    normalized: list[dict[str, Any]] = []
    material_refs: set[str] = set()
    source_ranks: set[int] = set()
    target_ranks: set[int] = set()
    for index, value in enumerate(values):
        field = f"moves[{index}]"
        check_record_keys(
            value,
            field=field,
            allowed=_MOVE_FIELDS,
            required=_REQUIRED_MOVE_FIELDS,
        )
        material_ref = compact_token(
            value["material_ref"],
            field=f"{field}.material_ref",
        )
        if material_ref in protected_material_refs:
            raise ValueError(f"{field}.material_ref is protected")
        if material_ref in material_refs:
            raise ValueError("moves material_ref values must be unique")
        from_rank = positive_int(value["from_rank"], field=f"{field}.from_rank")
        to_rank = positive_int(value["to_rank"], field=f"{field}.to_rank")
        if from_rank > target_window_size or to_rank > target_window_size:
            raise ValueError(f"{field} ranks must stay within target_window_size")
        if abs(from_rank - to_rank) > max_rank_displacement:
            raise ValueError(f"{field} exceeds max_rank_displacement")
        if from_rank == to_rank:
            raise ValueError(f"{field} must change rank")
        if from_rank in source_ranks:
            raise ValueError("moves from_rank values must be unique")
        if to_rank in target_ranks:
            raise ValueError("moves to_rank values must be unique")
        material_refs.add(material_ref)
        source_ranks.add(from_rank)
        target_ranks.add(to_rank)
        normalized.append(
            {
                "material_ref": material_ref,
                "from_rank": from_rank,
                "to_rank": to_rank,
                "reason_code": compact_token(
                    value["reason_code"],
                    field=f"{field}.reason_code",
                ),
                "evidence_refs": token_list(
                    value.get("evidence_refs"),
                    field=f"{field}.evidence_refs",
                    max_items=10,
                ),
            }
        )
    return sorted(
        normalized,
        key=lambda item: json.dumps(
            item,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def build_material_rerank_proposal(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    proposal_id: str,
    inventory_ref: str,
    decision_evidence_ref: str,
    observed_at: str,
    target_window_size: int,
    max_moved_items: int,
    max_rank_displacement: int,
    moves: Sequence[Mapping[str, Any]] | None = None,
    protected_material_refs: Sequence[str] | None = None,
    no_change_reason: str | None = None,
) -> dict[str, Any]:
    """Propose a bounded delta; never rewrite or apply a whole material queue."""

    window = positive_int(target_window_size, field="target_window_size")
    max_moved = positive_int(max_moved_items, field="max_moved_items")
    max_displacement = positive_int(
        max_rank_displacement,
        field="max_rank_displacement",
    )
    protected = token_list(
        protected_material_refs,
        field="protected_material_refs",
    )
    normalized_moves = _normalize_moves(
        moves,
        protected_material_refs=set(protected),
        max_moved_items=max_moved,
        max_rank_displacement=max_displacement,
        target_window_size=window,
    )
    if not normalized_moves and no_change_reason is None:
        raise ValueError("no_change_reason is required when moves is empty")
    if normalized_moves and no_change_reason is not None:
        raise ValueError("no_change_reason is only valid when moves is empty")

    proposal: dict[str, Any] = {
        "schema_version": MATERIAL_RERANK_PROPOSAL_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "proposal_id": compact_token(proposal_id, field="proposal_id"),
        "inventory_ref": compact_token(inventory_ref, field="inventory_ref"),
        "decision_evidence_ref": compact_token(
            decision_evidence_ref,
            field="decision_evidence_ref",
        ),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="rerank_proposal", project_scoped=project_scope is not None),
        "constraints": {
            "target_window_size": window,
            "max_moved_items": max_moved,
            "max_rank_displacement": max_displacement,
            "protected_material_refs": protected,
        },
        "moves": normalized_moves,
        "no_change": not normalized_moves,
        "apply_authorized": False,
        "raw_content_captured": False,
    }
    if no_change_reason is not None:
        proposal["no_change_reason"] = compact_text(
            no_change_reason,
            field="no_change_reason",
        )
    proposal["proposal_ref"] = packet_ref("material-rerank", proposal)
    return proposal


def build_material_rerank_apply_receipt(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    receipt_id: str,
    proposal_ref: str,
    observed_at: str,
    status: str,
    before_revision: str,
    after_revision: str,
    owner_gate_ref: str,
    validation_ref: str,
    applied_material_refs: Sequence[str] | None = None,
    rollback_ref: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Record the result of an owner-gated apply, rejection, no-op, or rollback."""

    normalized_status = compact_token(status, field="status")
    if normalized_status not in APPLY_STATUSES:
        raise ValueError(f"unsupported status: {normalized_status}")
    before = compact_token(before_revision, field="before_revision")
    after = compact_token(after_revision, field="after_revision")
    applied_refs = token_list(
        applied_material_refs,
        field="applied_material_refs",
        max_items=MATERIAL_RERANK_RECEIPT_MAX_REFS,
    )

    if normalized_status == "applied":
        if not applied_refs:
            raise ValueError("applied status requires applied_material_refs")
        if before == after:
            raise ValueError("applied status requires a new after_revision")
        if rollback_ref is None:
            raise ValueError("applied status requires rollback_ref")
    elif normalized_status == "rolled_back":
        if before == after:
            raise ValueError("rolled_back status requires a new after_revision")
        if rollback_ref is None:
            raise ValueError("rolled_back status requires rollback_ref")
    else:
        if applied_refs:
            raise ValueError(
                f"{normalized_status} status cannot include applied_material_refs"
            )
        if before != after:
            raise ValueError(
                f"{normalized_status} status must preserve the store revision"
            )
    if normalized_status == "rejected" and rejection_reason is None:
        raise ValueError("rejected status requires rejection_reason")
    if normalized_status != "rejected" and rejection_reason is not None:
        raise ValueError("rejection_reason is only valid for rejected status")

    receipt: dict[str, Any] = {
        "schema_version": MATERIAL_RERANK_APPLY_RECEIPT_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "receipt_id": compact_token(receipt_id, field="receipt_id"),
        "proposal_ref": compact_token(proposal_ref, field="proposal_ref"),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "status": normalized_status,
        "before_revision": before,
        "after_revision": after,
        "owner_gate_ref": compact_token(owner_gate_ref, field="owner_gate_ref"),
        "validation_ref": compact_token(validation_ref, field="validation_ref"),
        "applied_material_refs": applied_refs,
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="rerank_apply_receipt", project_scoped=project_scope is not None),
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    if rollback_ref is not None:
        receipt["rollback_ref"] = compact_token(
            rollback_ref,
            field="rollback_ref",
        )
    if rejection_reason is not None:
        receipt["rejection_reason"] = compact_text(
            rejection_reason,
            field="rejection_reason",
        )
    receipt["receipt_ref"] = packet_ref("material-rerank-apply", receipt)
    return receipt
