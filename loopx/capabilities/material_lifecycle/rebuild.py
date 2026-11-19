"""Lossless ranked-entry rebuild contracts for material queues."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._validation import (
    APPLY_STATUSES,
    capability_contract,
    check_record_keys,
    compact_text,
    compact_token,
    iso_timestamp,
    material_catalog_entry_limit,
    packet_ref,
    positive_int,
    token_list,
)

MATERIAL_RANKED_ENTRY_REBUILD_PLAN_SCHEMA_VERSION = (
    "material_ranked_entry_rebuild_plan_v0"
)
MATERIAL_RANKED_ENTRY_REBUILD_APPLY_RECEIPT_SCHEMA_VERSION = (
    "material_ranked_entry_rebuild_apply_receipt_v0"
)

_SOURCE_ENTRY_FIELDS = {"entry_ref", "material_refs", "rank"}
_REBUILT_ENTRY_FIELDS = {
    "evidence_refs",
    "material_refs",
    "reason_code",
    "source_entry_ref",
    "target_rank",
}
_RANK_ANCHOR_FIELDS = {"material_ref", "rank"}
def _ordered_token_list(
    values: Sequence[Any],
    *,
    field: str,
    max_items: int = 100,
) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of compact tokens")
    if not values:
        raise ValueError(f"{field} must contain at least one item")
    if len(values) > max_items:
        raise ValueError(f"{field} must contain at most {max_items} items")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = compact_token(value, field=f"{field}[]")
        if token in seen:
            raise ValueError(f"{field} values must be unique")
        seen.add(token)
        normalized.append(token)
    return normalized


def derive_material_ranked_entry_ref(
    *,
    source_entry_ref: str,
    material_refs: Sequence[str],
) -> str:
    """Derive a stable child reference from its source entry and ordered members."""

    source_ref = compact_token(source_entry_ref, field="source_entry_ref")
    members = _ordered_token_list(material_refs, field="material_refs")
    return packet_ref(
        "material-ranked-entry",
        {"source_entry_ref": source_ref, "material_refs": members},
    )


def _normalize_source_entries(
    values: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("source_entries must be a sequence of objects")
    if not values:
        raise ValueError("source_entries must contain at least one entry")

    normalized: list[dict[str, Any]] = []
    by_ref: dict[str, dict[str, Any]] = {}
    all_material_refs: set[str] = set()
    source_ranks: set[int] = set()
    for index, value in enumerate(values):
        field = f"source_entries[{index}]"
        check_record_keys(
            value,
            field=field,
            allowed=_SOURCE_ENTRY_FIELDS,
            required=_SOURCE_ENTRY_FIELDS,
        )
        entry_ref = compact_token(value["entry_ref"], field=f"{field}.entry_ref")
        if entry_ref in by_ref:
            raise ValueError("source_entries entry_ref values must be unique")
        rank = positive_int(value["rank"], field=f"{field}.rank")
        if rank in source_ranks:
            raise ValueError("source_entries rank values must be unique")
        material_refs = _ordered_token_list(
            value["material_refs"],
            field=f"{field}.material_refs",
        )
        duplicates = all_material_refs.intersection(material_refs)
        if duplicates:
            raise ValueError(
                "source material_refs must be globally unique: "
                + ", ".join(sorted(duplicates))
            )
        source_ranks.add(rank)
        all_material_refs.update(material_refs)
        entry = {
            "entry_ref": entry_ref,
            "rank": rank,
            "material_refs": material_refs,
        }
        by_ref[entry_ref] = entry
        normalized.append(entry)

    if sorted(source_ranks) != list(range(1, len(normalized) + 1)):
        raise ValueError("source_entries ranks must be contiguous from 1")
    return sorted(normalized, key=lambda item: item["rank"]), by_ref, all_material_refs


def _normalize_rebuilt_entries(
    values: Sequence[Mapping[str, Any]],
    *,
    source_by_ref: Mapping[str, Mapping[str, Any]],
    source_material_refs: set[str],
    max_materials_per_entry: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("rebuilt_entries must be a sequence of objects")
    if not values:
        raise ValueError("rebuilt_entries must contain at least one entry")

    grouped_materials: dict[str, set[str]] = {}
    normalized: list[dict[str, Any]] = []
    output_entry_refs: set[str] = set()
    output_material_refs: set[str] = set()
    target_ranks: set[int] = set()
    for index, value in enumerate(values):
        field = f"rebuilt_entries[{index}]"
        check_record_keys(
            value,
            field=field,
            allowed=_REBUILT_ENTRY_FIELDS,
            required={
                "material_refs",
                "reason_code",
                "source_entry_ref",
                "target_rank",
            },
        )
        source_entry_ref = compact_token(
            value["source_entry_ref"],
            field=f"{field}.source_entry_ref",
        )
        if source_entry_ref not in source_by_ref:
            raise ValueError(f"{field}.source_entry_ref is not in source_entries")
        target_rank = positive_int(
            value["target_rank"],
            field=f"{field}.target_rank",
        )
        if target_rank in target_ranks:
            raise ValueError("rebuilt_entries target_rank values must be unique")
        material_refs = _ordered_token_list(
            value["material_refs"],
            field=f"{field}.material_refs",
            max_items=max_materials_per_entry,
        )
        unknown = set(material_refs) - source_material_refs
        if unknown:
            raise ValueError(
                f"{field}.material_refs contains unknown refs: "
                + ", ".join(sorted(unknown))
            )
        duplicates = output_material_refs.intersection(material_refs)
        if duplicates:
            raise ValueError(
                "rebuilt material_refs must be globally unique: "
                + ", ".join(sorted(duplicates))
            )

        source_entry = source_by_ref[source_entry_ref]
        source_members = list(source_entry["material_refs"])
        if material_refs == source_members:
            entry_ref = source_entry_ref
        else:
            entry_ref = derive_material_ranked_entry_ref(
                source_entry_ref=source_entry_ref,
                material_refs=material_refs,
            )
        if entry_ref in output_entry_refs:
            raise ValueError("derived rebuilt entry_ref values must be unique")

        grouped_materials.setdefault(source_entry_ref, set()).update(material_refs)
        output_entry_refs.add(entry_ref)
        output_material_refs.update(material_refs)
        target_ranks.add(target_rank)
        normalized.append(
            {
                "entry_ref": entry_ref,
                "source_entry_ref": source_entry_ref,
                "target_rank": target_rank,
                "material_refs": material_refs,
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

    if sorted(target_ranks) != list(range(1, len(normalized) + 1)):
        raise ValueError("rebuilt_entries target ranks must be contiguous from 1")
    if output_material_refs != source_material_refs:
        missing = sorted(source_material_refs - output_material_refs)
        raise ValueError(
            "rebuilt_entries must cover every source material_ref exactly once"
            + (f"; missing: {', '.join(missing)}" if missing else "")
        )
    for source_entry_ref, source_entry in source_by_ref.items():
        if grouped_materials[source_entry_ref] != set(source_entry["material_refs"]):
            raise ValueError(
                "rebuilt child entries must preserve exact source membership "
                f"for {source_entry_ref}"
            )
    return sorted(normalized, key=lambda item: item["target_rank"]), output_entry_refs


def _normalize_rank_anchors(
    values: Sequence[Mapping[str, Any]] | None,
    *,
    source_material_refs: set[str],
    rebuilt_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("protected_rank_anchors must be a sequence of objects")

    material_locations = {
        str(material_ref): int(entry["target_rank"])
        for entry in rebuilt_entries
        for material_ref in entry["material_refs"]
    }
    normalized: list[dict[str, Any]] = []
    material_refs: set[str] = set()
    ranks: set[int] = set()
    for index, value in enumerate(values):
        field = f"protected_rank_anchors[{index}]"
        check_record_keys(
            value,
            field=field,
            allowed=_RANK_ANCHOR_FIELDS,
            required=_RANK_ANCHOR_FIELDS,
        )
        material_ref = compact_token(
            value["material_ref"],
            field=f"{field}.material_ref",
        )
        rank = positive_int(value["rank"], field=f"{field}.rank")
        if material_ref not in source_material_refs:
            raise ValueError(f"{field}.material_ref is not in source_entries")
        if material_ref in material_refs or rank in ranks:
            raise ValueError("protected_rank_anchors materials and ranks must be unique")
        if material_locations[material_ref] != rank:
            raise ValueError(f"{field} does not match the rebuilt target rank")
        material_refs.add(material_ref)
        ranks.add(rank)
        normalized.append({"material_ref": material_ref, "rank": rank})
    return sorted(normalized, key=lambda item: item["rank"])


def build_material_ranked_entry_rebuild_plan(
    *,
    goal_id: str,
    plan_id: str,
    inventory_ref: str,
    source_revision: str,
    observed_at: str,
    source_entries: Sequence[Mapping[str, Any]],
    rebuilt_entries: Sequence[Mapping[str, Any]],
    max_materials_per_entry: int | None = None,
    catalog: Mapping[str, Any] | None = None,
    top_window_size: int = 30,
    protected_rank_anchors: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a lossless structural rebuild without applying store changes.

    When ``max_materials_per_entry`` is omitted, the cap is read from
    ``catalog["rankings"]["ranked_entries"]["constraints"]``; without a catalog
    it falls back to the default of 3.
    """

    entry_limit = material_catalog_entry_limit(
        explicit=max_materials_per_entry,
        catalog=catalog,
    )
    window_size = positive_int(top_window_size, field="top_window_size")
    normalized_source, source_by_ref, source_material_refs = (
        _normalize_source_entries(source_entries)
    )
    normalized_rebuilt, output_entry_refs = _normalize_rebuilt_entries(
        rebuilt_entries,
        source_by_ref=source_by_ref,
        source_material_refs=source_material_refs,
        max_materials_per_entry=entry_limit,
    )
    if window_size > len(normalized_rebuilt):
        raise ValueError("top_window_size cannot exceed rebuilt entry count")
    anchors = _normalize_rank_anchors(
        protected_rank_anchors,
        source_material_refs=source_material_refs,
        rebuilt_entries=normalized_rebuilt,
    )

    split_source_refs = sorted(
        source_ref
        for source_ref in source_by_ref
        if sum(
            entry["source_entry_ref"] == source_ref for entry in normalized_rebuilt
        )
        > 1
    )
    oversized_source_refs = sorted(
        source_ref
        for source_ref, source_entry in source_by_ref.items()
        if len(source_entry["material_refs"]) > entry_limit
    )
    if not set(oversized_source_refs).issubset(split_source_refs):
        raise ValueError("every oversized source entry must create child entries")

    plan: dict[str, Any] = {
        "schema_version": MATERIAL_RANKED_ENTRY_REBUILD_PLAN_SCHEMA_VERSION,
        "goal_id": compact_token(goal_id, field="goal_id"),
        "plan_id": compact_token(plan_id, field="plan_id"),
        "inventory_ref": compact_token(inventory_ref, field="inventory_ref"),
        "source_revision": compact_token(source_revision, field="source_revision"),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="ranked_entry_rebuild_plan"),
        "constraints": {
            "max_materials_per_entry": entry_limit,
            "top_window_size": window_size,
            "protected_rank_anchors": anchors,
        },
        "source_entries": normalized_source,
        "rebuilt_entries": normalized_rebuilt,
        "summary": {
            "source_entry_count": len(normalized_source),
            "rebuilt_entry_count": len(normalized_rebuilt),
            "material_ref_count": len(source_material_refs),
            "split_source_entry_count": len(split_source_refs),
            "oversized_source_entry_count": len(oversized_source_refs),
            "top_window_entry_count": window_size,
            "ranked_backlog_entry_count": len(normalized_rebuilt) - window_size,
        },
        "verification": {
            "complete_coverage_verified": True,
            "unique_membership_verified": True,
            "source_lineage_membership_verified": True,
            "contiguous_rank_sequence_verified": True,
            "entry_budget_verified": True,
            "deterministic_entry_refs_verified": len(output_entry_refs)
            == len(normalized_rebuilt),
            "protected_rank_anchors_verified": True,
        },
        "apply_authorized": False,
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    plan["plan_ref"] = packet_ref("material-ranked-entry-rebuild", plan)
    return plan


def build_material_ranked_entry_rebuild_apply_receipt(
    *,
    goal_id: str,
    receipt_id: str,
    plan_ref: str,
    observed_at: str,
    status: str,
    before_revision: str,
    after_revision: str,
    owner_gate_ref: str,
    validation_ref: str,
    source_entry_count: int,
    rebuilt_entry_count: int,
    material_ref_count: int,
    top_window_size: int,
    rollback_ref: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Record an owner-gated structural rebuild result."""

    normalized_status = compact_token(status, field="status")
    if normalized_status not in APPLY_STATUSES:
        raise ValueError(f"unsupported status: {normalized_status}")
    before = compact_token(before_revision, field="before_revision")
    after = compact_token(after_revision, field="after_revision")
    source_count = positive_int(source_entry_count, field="source_entry_count")
    rebuilt_count = positive_int(rebuilt_entry_count, field="rebuilt_entry_count")
    material_count = positive_int(material_ref_count, field="material_ref_count")
    window_size = positive_int(top_window_size, field="top_window_size")
    if rebuilt_count < source_count:
        raise ValueError("rebuilt_entry_count cannot be smaller than source_entry_count")
    if window_size > rebuilt_count:
        raise ValueError("top_window_size cannot exceed rebuilt_entry_count")

    if normalized_status in {"applied", "rolled_back"}:
        if before == after:
            raise ValueError(f"{normalized_status} status requires a new revision")
        if rollback_ref is None:
            raise ValueError(f"{normalized_status} status requires rollback_ref")
    elif before != after:
        raise ValueError(f"{normalized_status} status must preserve revision")
    if normalized_status == "rejected" and rejection_reason is None:
        raise ValueError("rejected status requires rejection_reason")
    if normalized_status != "rejected" and rejection_reason is not None:
        raise ValueError("rejection_reason is only valid for rejected status")

    receipt: dict[str, Any] = {
        "schema_version": (
            MATERIAL_RANKED_ENTRY_REBUILD_APPLY_RECEIPT_SCHEMA_VERSION
        ),
        "goal_id": compact_token(goal_id, field="goal_id"),
        "receipt_id": compact_token(receipt_id, field="receipt_id"),
        "plan_ref": compact_token(plan_ref, field="plan_ref"),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "status": normalized_status,
        "before_revision": before,
        "after_revision": after,
        "owner_gate_ref": compact_token(owner_gate_ref, field="owner_gate_ref"),
        "validation_ref": compact_token(validation_ref, field="validation_ref"),
        "summary": {
            "source_entry_count": source_count,
            "rebuilt_entry_count": rebuilt_count,
            "material_ref_count": material_count,
            "top_window_entry_count": window_size,
            "ranked_backlog_entry_count": rebuilt_count - window_size,
        },
        "verification": {
            "complete_coverage_verified": True,
            "unique_membership_verified": True,
            "contiguous_rank_sequence_verified": True,
            "entry_budget_verified": True,
            "deterministic_entry_refs_verified": True,
        },
        "visibility": "public_safe",
        "capability": capability_contract(
            packet_role="ranked_entry_rebuild_apply_receipt"
        ),
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
    receipt["receipt_ref"] = packet_ref(
        "material-ranked-entry-rebuild-apply",
        receipt,
    )
    return receipt
