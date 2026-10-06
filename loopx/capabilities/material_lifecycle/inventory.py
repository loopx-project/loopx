"""Inventory and backup-safe migration contracts for material stores."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .ownership import MaterialProjectScope, material_owner_fields

from ._validation import (
    capability_contract,
    compact_token,
    iso_timestamp,
    nonnegative_int,
    packet_ref,
    token_list,
)

MATERIAL_STORE_INVENTORY_SCHEMA_VERSION = "material_store_inventory_v0"
MATERIAL_MIGRATION_PLAN_SCHEMA_VERSION = "material_migration_plan_v0"

MATERIAL_LIFECYCLE_STATES = {
    "active",
    "archived",
    "candidate",
    "carryover",
    "unread",
}


def build_material_store_inventory(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    store_id: str,
    store_revision: str,
    observed_at: str,
    source_snapshot_ref: str,
    backup_ref: str,
    source_digest: str,
    lifecycle_counts: Mapping[str, int],
    parse_error_refs: Sequence[str] | None = None,
    stable_ids_verified: bool,
    backup_verified: bool,
) -> dict[str, Any]:
    """Build a read-only inventory without copying material content or locations."""

    if not isinstance(lifecycle_counts, Mapping):
        raise TypeError("lifecycle_counts must be an object")
    unexpected_states = sorted(set(lifecycle_counts) - MATERIAL_LIFECYCLE_STATES)
    if unexpected_states:
        raise ValueError(
            "lifecycle_counts contains unsupported states: "
            + ", ".join(unexpected_states)
        )
    counts = {
        state: nonnegative_int(lifecycle_counts.get(state, 0), field=f"counts.{state}")
        for state in sorted(MATERIAL_LIFECYCLE_STATES)
    }
    if not isinstance(stable_ids_verified, bool):
        raise ValueError("stable_ids_verified must be a boolean")
    if not isinstance(backup_verified, bool):
        raise ValueError("backup_verified must be a boolean")

    inventory: dict[str, Any] = {
        "schema_version": MATERIAL_STORE_INVENTORY_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "store_id": compact_token(store_id, field="store_id"),
        "store_revision": compact_token(store_revision, field="store_revision"),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "source_snapshot_ref": compact_token(
            source_snapshot_ref,
            field="source_snapshot_ref",
        ),
        "backup_ref": compact_token(backup_ref, field="backup_ref"),
        "source_digest": compact_token(source_digest, field="source_digest"),
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="inventory", project_scoped=project_scope is not None),
        "lifecycle_counts": counts,
        "item_count": sum(counts.values()),
        "parse_error_refs": token_list(
            parse_error_refs,
            field="parse_error_refs",
        ),
        "stable_ids_verified": stable_ids_verified,
        "backup_verified": backup_verified,
        "dual_read_required": True,
        "source_mutation_authorized": False,
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    inventory["inventory_ref"] = packet_ref("material-inventory", inventory)
    return inventory


def build_material_migration_plan(
    *,
    goal_id: str,
    plan_id: str,
    inventory_ref: str,
    source_revision: str,
    target_schema: str,
    observed_at: str,
    backup_ref: str,
    rollback_ref: str,
    expected_item_count: int,
    protected_material_refs: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Plan a reversible migration while keeping the source store authoritative."""

    plan: dict[str, Any] = {
        "schema_version": MATERIAL_MIGRATION_PLAN_SCHEMA_VERSION,
        "goal_id": compact_token(goal_id, field="goal_id"),
        "plan_id": compact_token(plan_id, field="plan_id"),
        "inventory_ref": compact_token(inventory_ref, field="inventory_ref"),
        "source_revision": compact_token(
            source_revision,
            field="source_revision",
        ),
        "target_schema": compact_token(target_schema, field="target_schema"),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "backup_ref": compact_token(backup_ref, field="backup_ref"),
        "rollback_ref": compact_token(rollback_ref, field="rollback_ref"),
        "expected_item_count": nonnegative_int(
            expected_item_count,
            field="expected_item_count",
        ),
        "protected_material_refs": token_list(
            protected_material_refs,
            field="protected_material_refs",
        ),
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="migration_plan"),
        "phases": [
            "snapshot",
            "inventory",
            "dual_read",
            "reconcile",
            "owner_gate",
            "apply",
            "rollback_ready",
        ],
        "owner_gate_required": True,
        "source_mutation_authorized": False,
        "cutover_mode": "dual_read_then_owner_gated_apply",
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    plan["plan_ref"] = packet_ref("material-migration-plan", plan)
    return plan
