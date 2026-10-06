"""Completion contract joining candidate intake to its ranking disposition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .ownership import MaterialProjectScope, material_owner_fields

from ._validation import (
    capability_contract,
    compact_text,
    compact_token,
    iso_timestamp,
    packet_ref,
    positive_int,
    token_list,
)

MATERIAL_INTAKE_RANKING_SETTLEMENT_SCHEMA_VERSION = (
    "material_intake_ranking_settlement_v0"
)

_VALUE_CLASSIFICATIONS = {"high_value", "standard"}
_RANKING_DISPOSITIONS = {"top_window", "ranked_backlog", "no_change"}


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def build_material_intake_ranking_settlement(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    settlement_id: str,
    material_ref: str,
    observed_at: str,
    decision_evidence_ref: str,
    value_classification: str,
    ranking_disposition: str,
    intake_before_revision: str,
    intake_after_revision: str,
    ranking_before_revision: str,
    ranking_after_revision: str,
    intake_receipt_ref: str,
    ranking_receipt_ref: str,
    owner_gate_ref: str,
    validation_ref: str,
    top_window_size: int,
    authority_readback_verified: bool,
    ranking_source_contains_material_verified: bool,
    ranked_membership_verified: bool,
    target_rank: int | None = None,
    projection_receipt_ref: str | None = None,
    rollback_ref: str | None = None,
    displaced_to_backlog_refs: Sequence[str] | None = None,
    no_change_reason: str | None = None,
) -> dict[str, Any]:
    """Build a public-safe receipt only after intake and ranking are settled."""

    classification = compact_token(
        value_classification,
        field="value_classification",
    )
    if classification not in _VALUE_CLASSIFICATIONS:
        raise ValueError(f"unsupported value_classification: {classification}")
    disposition = compact_token(
        ranking_disposition,
        field="ranking_disposition",
    )
    if disposition not in _RANKING_DISPOSITIONS:
        raise ValueError(f"unsupported ranking_disposition: {disposition}")
    if classification == "high_value" and disposition == "no_change":
        raise ValueError("high_value material requires ranked membership")

    normalized_material_ref = compact_token(material_ref, field="material_ref")
    normalized_intake_receipt_ref = compact_token(
        intake_receipt_ref,
        field="intake_receipt_ref",
    )
    normalized_ranking_receipt_ref = compact_token(
        ranking_receipt_ref,
        field="ranking_receipt_ref",
    )
    if normalized_intake_receipt_ref == normalized_ranking_receipt_ref:
        raise ValueError("intake and ranking receipts must remain separate")

    intake_before = compact_token(
        intake_before_revision,
        field="intake_before_revision",
    )
    intake_after = compact_token(
        intake_after_revision,
        field="intake_after_revision",
    )
    ranking_before = compact_token(
        ranking_before_revision,
        field="ranking_before_revision",
    )
    ranking_after = compact_token(
        ranking_after_revision,
        field="ranking_after_revision",
    )
    if intake_before == intake_after:
        raise ValueError("candidate intake must advance the authority revision")
    readback_verified = _required_bool(
        authority_readback_verified,
        field="authority_readback_verified",
    )
    source_contains_material = _required_bool(
        ranking_source_contains_material_verified,
        field="ranking_source_contains_material_verified",
    )
    membership_verified = _required_bool(
        ranked_membership_verified,
        field="ranked_membership_verified",
    )
    if not readback_verified:
        raise ValueError("authority_readback_verified must be true")
    if not source_contains_material:
        raise ValueError("ranking source must contain the candidate material")

    window_size = positive_int(top_window_size, field="top_window_size")
    displaced_refs = token_list(
        displaced_to_backlog_refs,
        field="displaced_to_backlog_refs",
    )
    if normalized_material_ref in displaced_refs:
        raise ValueError("the settled material cannot be displaced to backlog")
    normalized_target_rank: int | None = None
    if target_rank is not None:
        normalized_target_rank = positive_int(target_rank, field="target_rank")

    if disposition == "no_change":
        if normalized_target_rank is not None:
            raise ValueError("no_change cannot include target_rank")
        if membership_verified:
            raise ValueError("no_change cannot claim ranked membership")
        if displaced_refs:
            raise ValueError("no_change cannot displace ranked materials")
        if no_change_reason is None:
            raise ValueError("no_change requires no_change_reason")
        if ranking_before == ranking_after:
            if projection_receipt_ref is not None:
                raise ValueError(
                    "no_change without a shared rerank cannot include "
                    "projection_receipt_ref"
                )
            if rollback_ref is not None:
                raise ValueError(
                    "no_change without a shared rerank cannot include rollback_ref"
                )
        elif projection_receipt_ref is None or rollback_ref is None:
            raise ValueError(
                "no_change in a shared rerank requires projection and rollback refs"
            )
    else:
        if ranking_before == ranking_after:
            raise ValueError("ranked placement must advance the ranking revision")
        if normalized_target_rank is None:
            raise ValueError("ranked placement requires target_rank")
        if disposition == "top_window" and normalized_target_rank > window_size:
            raise ValueError("top_window target_rank exceeds top_window_size")
        if disposition == "ranked_backlog" and normalized_target_rank <= window_size:
            raise ValueError("ranked_backlog target_rank must follow the top window")
        if disposition == "ranked_backlog" and displaced_refs:
            raise ValueError("ranked_backlog cannot displace ranked materials")
        if not membership_verified:
            raise ValueError("ranked placement requires verified membership")
        if projection_receipt_ref is None:
            raise ValueError("ranked placement requires projection_receipt_ref")
        if rollback_ref is None:
            raise ValueError("ranked placement requires rollback_ref")
        if no_change_reason is not None:
            raise ValueError("no_change_reason is only valid for no_change")

    settlement: dict[str, Any] = {
        "schema_version": MATERIAL_INTAKE_RANKING_SETTLEMENT_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "settlement_id": compact_token(settlement_id, field="settlement_id"),
        "material_ref": normalized_material_ref,
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "decision_evidence_ref": compact_token(
            decision_evidence_ref,
            field="decision_evidence_ref",
        ),
        "value_classification": classification,
        "ranking_disposition": disposition,
        "intake_before_revision": intake_before,
        "intake_after_revision": intake_after,
        "ranking_before_revision": ranking_before,
        "ranking_after_revision": ranking_after,
        "intake_receipt_ref": normalized_intake_receipt_ref,
        "ranking_receipt_ref": normalized_ranking_receipt_ref,
        "owner_gate_ref": compact_token(
            owner_gate_ref,
            field="owner_gate_ref",
        ),
        "validation_ref": compact_token(
            validation_ref,
            field="validation_ref",
        ),
        "top_window_size": window_size,
        "target_rank": normalized_target_rank,
        "displaced_to_backlog_refs": displaced_refs,
        "status": "settled",
        "verification": {
            "candidate_intake_completed": True,
            "ranking_disposition_recorded": True,
            "ranking_source_contains_material_verified": True,
            "authority_readback_verified": True,
            "ranked_membership_verified": membership_verified,
        },
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="intake_ranking_settlement", project_scoped=project_scope is not None),
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    if projection_receipt_ref is not None:
        settlement["projection_receipt_ref"] = compact_token(
            projection_receipt_ref,
            field="projection_receipt_ref",
        )
    if rollback_ref is not None:
        settlement["rollback_ref"] = compact_token(
            rollback_ref,
            field="rollback_ref",
        )
    if no_change_reason is not None:
        settlement["no_change_reason"] = compact_text(
            no_change_reason,
            field="no_change_reason",
        )
    settlement["settlement_ref"] = packet_ref(
        "material-intake-ranking-settlement",
        settlement,
    )
    return settlement
