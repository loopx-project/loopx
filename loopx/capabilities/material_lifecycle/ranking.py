"""Bounded material rerank proposal and apply receipt contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .ownership import MaterialProjectScope, material_owner_fields

from ._validation import (
    capability_contract,
    check_record_keys,
    compact_text,
    compact_token,
    iso_timestamp,
    packet_ref,
    positive_int,
    token_list,
)

MATERIAL_RERANK_PROPOSAL_SCHEMA_VERSION = "material_rerank_proposal_v0"
MATERIAL_RERANK_APPLY_RECEIPT_SCHEMA_VERSION = "material_rerank_apply_receipt_v0"
MATERIAL_RERANK_RECEIPT_MAX_REFS = 100

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
_APPLY_STATUSES = {"applied", "no_change", "rejected", "rolled_back"}


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
    if normalized_status not in _APPLY_STATUSES:
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
