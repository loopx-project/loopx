"""Integrity and lineage contracts for derived material ranking state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._validation import (
    capability_contract,
    compact_token,
    iso_timestamp,
    packet_ref,
    positive_int,
)
from .lifecycle import MATERIAL_LIFECYCLE_RECEIPT_SCHEMA_VERSION
from .ownership import MaterialProjectScope, material_owner_fields

MATERIAL_RANKED_STATE_REPAIR_PLAN_SCHEMA_VERSION = (
    "material_ranked_state_repair_plan_v0"
)
MATERIAL_RANKED_STATE_REPAIR_APPLY_RECEIPT_SCHEMA_VERSION = (
    "material_ranked_state_repair_apply_receipt_v0"
)
MATERIAL_LIFECYCLE_RANKING_APPLY_RECEIPT_SCHEMA_VERSION = (
    "material_lifecycle_ranking_apply_receipt_v0"
)

_DERIVED_SUMMARY_FIELDS = (
    "rebuilt_entry_count",
    "material_ref_count",
    "top_window_size",
    "top_window_entry_count",
    "ranked_backlog_entry_count",
)
_RANKING_LINEAGE_SCHEMAS = {
    "material_ranked_entry_rebuild_apply_receipt_v0",
    "material_rerank_apply_receipt_v0",
    MATERIAL_RANKED_STATE_REPAIR_APPLY_RECEIPT_SCHEMA_VERSION,
    MATERIAL_LIFECYCLE_RANKING_APPLY_RECEIPT_SCHEMA_VERSION,
}


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _material_owner_from_packet(
    packet: Mapping[str, Any],
    *,
    field: str,
) -> dict[str, Any]:
    try:
        return material_owner_fields(
            goal_id=packet.get("goal_id"),
            project_scope=packet.get("project_scope"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must identify exactly one material owner") from exc


def _require_matching_material_owner(
    *,
    source_owner: Mapping[str, Any],
    target_owner: Mapping[str, Any],
    source_field: str,
) -> None:
    if source_owner != target_owner:
        raise ValueError(f"{source_field} material owner does not match apply receipt")


def _verified_packet_ref(
    packet: Mapping[str, Any],
    *,
    field: str,
    prefix: str,
) -> str:
    packet_key = field.rsplit(".", 1)[-1]
    claimed = compact_token(packet.get(packet_key), field=field)
    payload = {key: value for key, value in packet.items() if key != packet_key}
    if packet_ref(prefix, payload) != claimed:
        raise ValueError(f"{field} does not match packet content")
    return claimed


def _normalize_ranked_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    top_window_size: int,
    max_materials_per_entry: int,
) -> list[dict[str, Any]]:
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise TypeError("entries must be a sequence of objects")
    positive_int(top_window_size, field="top_window_size")
    entry_limit = positive_int(
        max_materials_per_entry,
        field="max_materials_per_entry",
    )

    normalized: list[dict[str, Any]] = []
    entry_refs: set[str] = set()
    material_refs: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise TypeError(f"entries[{index}] must be an object")
        rank = positive_int(entry.get("rank"), field=f"entries[{index}].rank")
        entry_ref = compact_token(
            entry.get("entry_ref"),
            field=f"entries[{index}].entry_ref",
        )
        refs_value = entry.get("material_refs")
        if isinstance(refs_value, (str, bytes)) or not isinstance(
            refs_value,
            Sequence,
        ):
            raise TypeError(f"entries[{index}].material_refs must be a sequence")
        refs = [
            compact_token(ref, field=f"entries[{index}].material_refs[]")
            for ref in refs_value
        ]
        if not 1 <= len(refs) <= entry_limit:
            raise ValueError(
                f"entries[{index}].material_refs must contain between 1 and "
                f"{entry_limit} items"
            )
        if entry_ref in entry_refs:
            raise ValueError("entry_ref values must be unique")
        duplicates = material_refs.intersection(refs)
        if duplicates or len(refs) != len(set(refs)):
            raise ValueError("material_ref values must be globally unique")
        entry_refs.add(entry_ref)
        material_refs.update(refs)
        normalized.append({"entry_ref": entry_ref, "rank": rank, "material_refs": refs})

    normalized.sort(key=lambda item: int(item["rank"]))
    if [item["rank"] for item in normalized] != list(range(1, len(normalized) + 1)):
        raise ValueError("entry ranks must be contiguous from 1")
    return normalized


def inspect_material_ranked_state(
    *,
    entries: Sequence[Mapping[str, Any]],
    top_window_size: int = 30,
    max_materials_per_entry: int = 3,
) -> dict[str, Any]:
    """Derive content-free counts and a deterministic order digest."""

    normalized = _normalize_ranked_entries(
        entries,
        top_window_size=top_window_size,
        max_materials_per_entry=max_materials_per_entry,
    )
    window_size = positive_int(top_window_size, field="top_window_size")
    material_count = sum(len(item["material_refs"]) for item in normalized)
    top_window_entry_count = min(window_size, len(normalized))
    order_ref = packet_ref(
        "material-ranked-order",
        {"entries": normalized},
    )
    return {
        "summary": {
            "rebuilt_entry_count": len(normalized),
            "material_ref_count": material_count,
            "top_window_size": window_size,
            "top_window_entry_count": top_window_entry_count,
            "ranked_backlog_entry_count": len(normalized) - top_window_entry_count,
        },
        "order_ref": order_ref,
        "verification": {
            "complete_coverage_verified": True,
            "unique_membership_verified": True,
            "contiguous_rank_sequence_verified": True,
            "entry_budget_verified": True,
        },
    }


def build_material_ranked_state_repair_plan(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    plan_id: str,
    source_revision: str,
    observed_at: str,
    entries: Sequence[Mapping[str, Any]],
    current_summary: Mapping[str, Any],
    top_window_size: int = 30,
    max_materials_per_entry: int = 3,
) -> dict[str, Any]:
    """Plan a counts-only repair without changing ranking membership or order."""

    if not isinstance(current_summary, Mapping):
        raise TypeError("current_summary must be an object")
    inspection = inspect_material_ranked_state(
        entries=entries,
        top_window_size=top_window_size,
        max_materials_per_entry=max_materials_per_entry,
    )
    expected = inspection["summary"]
    changes: dict[str, dict[str, int | None]] = {}
    for field in _DERIVED_SUMMARY_FIELDS:
        before = current_summary.get(field)
        if before is not None and (
            isinstance(before, bool) or not isinstance(before, int)
        ):
            raise TypeError(f"current_summary.{field} must be an integer")
        after = int(expected[field])
        if before != after:
            changes[field] = {"before": before, "after": after}

    plan: dict[str, Any] = {
        "schema_version": MATERIAL_RANKED_STATE_REPAIR_PLAN_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "plan_id": compact_token(plan_id, field="plan_id"),
        "source_revision": compact_token(
            source_revision,
            field="source_revision",
        ),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "order_ref": inspection["order_ref"],
        "expected_summary": expected,
        "changed_summary_fields": changes,
        "repair_required": bool(changes),
        "apply_authorized": False,
        "verification": inspection["verification"],
        "visibility": "public_safe",
        "capability": capability_contract(
            packet_role="ranked_state_repair_plan",
            project_scoped=project_scope is not None,
        ),
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    plan["plan_ref"] = packet_ref("material-ranked-state-repair", plan)
    return plan


def build_material_ranked_state_repair_apply_receipt(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    receipt_id: str,
    plan: Mapping[str, Any],
    observed_at: str,
    before_revision: str,
    after_revision: str,
    owner_gate_ref: str,
    validation_ref: str,
    rollback_ref: str,
    authority_readback_verified: bool,
) -> dict[str, Any]:
    """Bind a summary-only repair to its new authority revision."""

    if plan.get("schema_version") != MATERIAL_RANKED_STATE_REPAIR_PLAN_SCHEMA_VERSION:
        raise ValueError("plan has an unsupported schema_version")
    if not plan.get("repair_required"):
        raise ValueError("plan does not require a repair")
    target_owner = material_owner_fields(
        goal_id=goal_id,
        project_scope=project_scope,
    )
    plan_owner = _material_owner_from_packet(plan, field="plan")
    _require_matching_material_owner(
        source_owner=plan_owner,
        target_owner=target_owner,
        source_field="plan",
    )
    before = compact_token(before_revision, field="before_revision")
    after = compact_token(after_revision, field="after_revision")
    if before == after:
        raise ValueError("applied repair requires a new revision")
    if not _required_bool(
        authority_readback_verified,
        field="authority_readback_verified",
    ):
        raise ValueError("authority_readback_verified must be true")
    source_revision = compact_token(
        plan.get("source_revision"),
        field="plan.source_revision",
    )
    if source_revision != before:
        raise ValueError("plan source_revision does not match before_revision")

    plan_ref = _verified_packet_ref(
        plan,
        field="plan.plan_ref",
        prefix="material-ranked-state-repair",
    )
    receipt: dict[str, Any] = {
        "schema_version": MATERIAL_RANKED_STATE_REPAIR_APPLY_RECEIPT_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "receipt_id": compact_token(receipt_id, field="receipt_id"),
        "plan_ref": plan_ref,
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "status": "applied",
        "before_revision": before,
        "after_revision": after,
        "owner_gate_ref": compact_token(
            owner_gate_ref,
            field="owner_gate_ref",
        ),
        "validation_ref": compact_token(validation_ref, field="validation_ref"),
        "rollback_ref": compact_token(rollback_ref, field="rollback_ref"),
        "order_ref": compact_token(plan.get("order_ref"), field="plan.order_ref"),
        "summary": dict(plan["expected_summary"]),
        "changed_summary_fields": dict(plan["changed_summary_fields"]),
        "verification": {
            **dict(plan["verification"]),
            "ranking_membership_unchanged_verified": True,
            "ranking_order_unchanged_verified": True,
            "authority_readback_verified": True,
        },
        "visibility": "public_safe",
        "capability": capability_contract(
            packet_role="ranked_state_repair_apply_receipt",
            project_scoped=project_scope is not None,
        ),
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    receipt["receipt_ref"] = packet_ref(
        "material-ranked-state-repair-apply",
        receipt,
    )
    return receipt


def build_material_lifecycle_ranking_apply_receipt(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    receipt_id: str,
    lifecycle_receipt: Mapping[str, Any],
    observed_at: str,
    before_revision: str,
    after_revision: str,
    owner_gate_ref: str,
    validation_ref: str,
    rollback_ref: str,
    before_entries: Sequence[Mapping[str, Any]],
    after_entries: Sequence[Mapping[str, Any]],
    top_window_size: int = 30,
    max_materials_per_entry: int = 3,
    authority_readback_verified: bool = False,
) -> dict[str, Any]:
    """Verify that an archive transition removes only its ranked material."""

    if (
        lifecycle_receipt.get("schema_version")
        != MATERIAL_LIFECYCLE_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError("lifecycle_receipt has an unsupported schema_version")
    if lifecycle_receipt.get("to_state") != "archived":
        raise ValueError("lifecycle_receipt must archive the material")
    material_ref = compact_token(
        lifecycle_receipt.get("material_ref"),
        field="lifecycle_receipt.material_ref",
    )
    lifecycle_receipt_ref = _verified_packet_ref(
        lifecycle_receipt,
        field="lifecycle_receipt.receipt_ref",
        prefix="material-lifecycle",
    )
    target_owner = material_owner_fields(
        goal_id=goal_id,
        project_scope=project_scope,
    )
    lifecycle_owner = _material_owner_from_packet(
        lifecycle_receipt,
        field="lifecycle_receipt",
    )
    _require_matching_material_owner(
        source_owner=lifecycle_owner,
        target_owner=target_owner,
        source_field="lifecycle_receipt",
    )
    before_state = _normalize_ranked_entries(
        before_entries,
        top_window_size=top_window_size,
        max_materials_per_entry=max_materials_per_entry,
    )
    after_state = _normalize_ranked_entries(
        after_entries,
        top_window_size=top_window_size,
        max_materials_per_entry=max_materials_per_entry,
    )
    before_order = [ref for entry in before_state for ref in entry["material_refs"]]
    after_order = [ref for entry in after_state for ref in entry["material_refs"]]
    if before_order.count(material_ref) != 1:
        raise ValueError("archived material must appear exactly once before apply")
    expected_after_order = [ref for ref in before_order if ref != material_ref]
    if after_order != expected_after_order:
        raise ValueError(
            "after_entries must remove only the archived material and preserve order"
        )

    target_entry = next(
        entry for entry in before_state if material_ref in entry["material_refs"]
    )
    expected_entries = []
    for entry in before_state:
        refs = [ref for ref in entry["material_refs"] if ref != material_ref]
        if refs:
            expected_entries.append(
                {"entry_ref": entry["entry_ref"], "material_refs": refs}
            )
    actual_entries = [
        {"entry_ref": entry["entry_ref"], "material_refs": entry["material_refs"]}
        for entry in after_state
    ]
    if actual_entries != expected_entries:
        raise ValueError(
            "after_entries must preserve remaining entry membership and identity"
        )

    before_inspection = inspect_material_ranked_state(
        entries=before_entries,
        top_window_size=top_window_size,
        max_materials_per_entry=max_materials_per_entry,
    )
    after_inspection = inspect_material_ranked_state(
        entries=after_entries,
        top_window_size=top_window_size,
        max_materials_per_entry=max_materials_per_entry,
    )
    before = compact_token(before_revision, field="before_revision")
    after = compact_token(after_revision, field="after_revision")
    if before == after:
        raise ValueError("applied lifecycle ranking change requires a new revision")
    if not _required_bool(
        authority_readback_verified,
        field="authority_readback_verified",
    ):
        raise ValueError("authority_readback_verified must be true")

    receipt: dict[str, Any] = {
        "schema_version": MATERIAL_LIFECYCLE_RANKING_APPLY_RECEIPT_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "receipt_id": compact_token(receipt_id, field="receipt_id"),
        "lifecycle_receipt_ref": lifecycle_receipt_ref,
        "material_ref": material_ref,
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "status": "applied",
        "before_revision": before,
        "after_revision": after,
        "owner_gate_ref": compact_token(
            owner_gate_ref,
            field="owner_gate_ref",
        ),
        "validation_ref": compact_token(validation_ref, field="validation_ref"),
        "rollback_ref": compact_token(rollback_ref, field="rollback_ref"),
        "previous_rank": int(target_entry["rank"]),
        "before_order_ref": before_inspection["order_ref"],
        "after_order_ref": after_inspection["order_ref"],
        "before_summary": before_inspection["summary"],
        "summary": after_inspection["summary"],
        "verification": {
            **dict(after_inspection["verification"]),
            "archived_membership_removed_verified": True,
            "remaining_relative_order_preserved_verified": True,
            "remaining_entry_identity_preserved_verified": True,
            "authority_readback_verified": True,
        },
        "visibility": "public_safe",
        "capability": capability_contract(
            packet_role="lifecycle_ranking_apply_receipt",
            project_scoped=project_scope is not None,
        ),
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    receipt["receipt_ref"] = packet_ref(
        "material-lifecycle-ranking-apply",
        receipt,
    )
    return receipt


def resolve_material_ranking_lineage_receipt(
    *,
    receipts: Sequence[Mapping[str, Any]],
    authority_revision: str,
) -> dict[str, Any]:
    """Resolve the unique applied ranking receipt for an authority revision."""

    if isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise TypeError("receipts must be a sequence of objects")
    revision = compact_token(authority_revision, field="authority_revision")
    matches = [
        receipt
        for receipt in receipts
        if isinstance(receipt, Mapping)
        and receipt.get("schema_version") in _RANKING_LINEAGE_SCHEMAS
        and receipt.get("status") == "applied"
        and receipt.get("after_revision") == revision
    ]
    if len(matches) != 1:
        raise ValueError(
            "authority revision must resolve to exactly one ranking lineage receipt"
        )
    receipt = dict(matches[0])
    compact_token(receipt.get("receipt_ref"), field="receipt.receipt_ref")
    return receipt
