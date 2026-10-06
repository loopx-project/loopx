"""Owner-gated, source-backed candidate intake for managed material stores."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .ownership import (
    MaterialProjectScope, material_owner_fields, verify_project_material_write,
)

from ._validation import (
    capability_contract,
    check_record_keys,
    compact_token,
    iso_timestamp,
    nonnegative_int,
    packet_ref,
    positive_int,
)
from .apply import MaterialAuthoritySnapshot, MaterialAuthorityTransition

MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION = (
    "material_candidate_intake_proposal_v0"
)
MATERIAL_CANDIDATE_INTAKE_APPLY_RECEIPT_SCHEMA_VERSION = (
    "material_candidate_intake_apply_receipt_v0"
)
MATERIAL_CANDIDATE_INTAKE_ROLLBACK_RECEIPT_SCHEMA_VERSION = (
    "material_candidate_intake_rollback_receipt_v0"
)


@dataclass(frozen=True)
class MaterialStagedCandidate:
    """One append staged in a new immutable authority revision."""

    provider_id: str
    store_id: str
    source_revision: str
    target_revision: str
    material_ref: str
    candidate_record_ref: str
    content_backing_ref: str
    content_digest: str
    content_size_bytes: int
    atomic_write_verified: bool = False
    readback_verified: bool = False


@dataclass(frozen=True)
class MaterialCandidateAppendReconciliation:
    """Proof that staging appended exactly one candidate without rewriting peers."""

    provider_id: str
    store_id: str
    source_revision: str
    target_revision: str
    material_ref: str
    before_item_count: int
    after_item_count: int
    validation_ref: str
    stable_ids_verified: bool = False
    existing_records_unchanged: bool = False
    duplicate_material_count: int = 0


@dataclass(frozen=True)
class MaterialCandidateReadback:
    """Public-safe metadata read from a specific immutable store revision."""

    provider_id: str
    store_id: str
    authority_revision: str
    material_ref: str
    lifecycle_state: str
    source_ref: str
    source_revision: str
    exact_read_ref: str
    candidate_record_ref: str
    content_backing_ref: str
    content_digest: str
    content_size_bytes: int
    readback_verified: bool = False


class MaterialCandidateIntakeProvider(Protocol):
    """Private adapter boundary for content-backed candidate appends."""

    provider_id: str

    def inspect_authority(
        self,
        *,
        store_id: str,
        observed_at: str,
    ) -> MaterialAuthoritySnapshot: ...

    def read_candidate(
        self,
        *,
        store_id: str,
        authority_revision: str,
        material_ref: str,
        observed_at: str,
    ) -> MaterialCandidateReadback | None: ...

    def stage_candidate(
        self,
        *,
        store_id: str,
        proposal_ref: str,
        source_revision: str,
        material_ref: str,
        source_ref: str,
        source_material_revision: str,
        exact_read_ref: str,
        content: bytes,
        owner_gate_ref: str,
        observed_at: str,
    ) -> MaterialStagedCandidate: ...

    def reconcile_candidate_append(
        self,
        *,
        store_id: str,
        source_revision: str,
        target_revision: str,
        material_ref: str,
        observed_at: str,
    ) -> MaterialCandidateAppendReconciliation: ...

    def switch_authority(
        self,
        *,
        store_id: str,
        expected_revision: str,
        target_revision: str,
        owner_gate_ref: str,
        observed_at: str,
    ) -> MaterialAuthorityTransition: ...


def _verified(value: Any, *, field: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    if not value:
        raise ValueError(f"{field} must be verified")


def _content_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _provider_id(
    provider: MaterialCandidateIntakeProvider,
    *,
    expected: str,
) -> str:
    normalized = compact_token(expected, field="provider_id")
    actual = compact_token(
        getattr(provider, "provider_id", ""),
        field="provider.provider_id",
    )
    if actual != normalized:
        raise ValueError("candidate intake provider identity does not match")
    return str(normalized)


def _identity(
    value: Any,
    *,
    provider_id: str,
    store_id: str,
    field: str,
) -> None:
    if compact_token(value.provider_id, field=f"{field}.provider_id") != provider_id:
        raise ValueError(f"{field} provider identity does not match")
    if compact_token(value.store_id, field=f"{field}.store_id") != store_id:
        raise ValueError(f"{field} store identity does not match")


def _snapshot(
    provider: MaterialCandidateIntakeProvider,
    *,
    provider_id: str,
    store_id: str,
    observed_at: str,
) -> MaterialAuthoritySnapshot:
    snapshot = provider.inspect_authority(
        store_id=store_id,
        observed_at=observed_at,
    )
    if not isinstance(snapshot, MaterialAuthoritySnapshot):
        raise TypeError(
            "candidate intake provider must return MaterialAuthoritySnapshot"
        )
    _identity(
        snapshot,
        provider_id=provider_id,
        store_id=store_id,
        field="authority_snapshot",
    )
    compact_token(
        snapshot.authority_revision,
        field="authority_snapshot.authority_revision",
    )
    iso_timestamp(snapshot.observed_at, field="authority_snapshot.observed_at")
    return snapshot


def _readback(
    provider: MaterialCandidateIntakeProvider,
    *,
    provider_id: str,
    store_id: str,
    authority_revision: str,
    material_ref: str,
    observed_at: str,
) -> MaterialCandidateReadback | None:
    value = provider.read_candidate(
        store_id=store_id,
        authority_revision=authority_revision,
        material_ref=material_ref,
        observed_at=observed_at,
    )
    if value is None:
        return None
    if not isinstance(value, MaterialCandidateReadback):
        raise TypeError(
            "candidate intake provider must return MaterialCandidateReadback or None"
        )
    _identity(
        value,
        provider_id=provider_id,
        store_id=store_id,
        field="candidate_readback",
    )
    if (
        compact_token(
            value.authority_revision,
            field="candidate_readback.authority_revision",
        )
        != authority_revision
    ):
        raise ValueError("candidate readback authority revision does not match")
    if (
        compact_token(value.material_ref, field="candidate_readback.material_ref")
        != material_ref
    ):
        raise ValueError("candidate readback material reference does not match")
    return value


def build_material_candidate_intake_proposal(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    proposal_id: str,
    store_id: str,
    source_authority_revision: str,
    material_ref: str,
    source_ref: str,
    source_revision: str,
    exact_read_ref: str,
    content_digest: str,
    content_size_bytes: int,
    observed_at: str,
) -> dict[str, Any]:
    """Build a public-safe proposal from promoted exact-read evidence."""

    proposal: dict[str, Any] = {
        "schema_version": MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "proposal_id": compact_token(proposal_id, field="proposal_id"),
        "store_id": compact_token(store_id, field="store_id"),
        "source_authority_revision": compact_token(
            source_authority_revision,
            field="source_authority_revision",
        ),
        "material_ref": compact_token(material_ref, field="material_ref"),
        "source_ref": compact_token(source_ref, field="source_ref"),
        "source_revision": compact_token(
            source_revision,
            field="source_revision",
        ),
        "exact_read_ref": compact_token(exact_read_ref, field="exact_read_ref"),
        "content_digest": compact_token(
            content_digest,
            field="content_digest",
        ),
        "content_size_bytes": positive_int(
            content_size_bytes,
            field="content_size_bytes",
        ),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "lifecycle_state": "candidate",
        "owner_gate_required": True,
        "exact_read_verified": True,
        "content_backing_required": True,
        "source_mutation_authorized": False,
        "raw_content_captured": False,
        "private_locations_captured": False,
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="candidate_intake_proposal", project_scoped=project_scope is not None),
    }
    proposal["proposal_ref"] = packet_ref("material-candidate-intake", proposal)
    return proposal


def _packet_owner(value: Mapping[str, Any]) -> dict[str, Any]:
    owner = material_owner_fields(
        goal_id=value.get("goal_id"), project_scope=value.get("project_scope"),
    )
    if "project_scope" in owner:
        contract = value.get("capability")
        if not isinstance(contract, Mapping) or contract.get("scope") != "project":
            raise ValueError("project material packet must declare project capability scope")
    return owner


def _proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    check_record_keys(
        value,
        field="candidate_intake_proposal",
        allowed={
            "capability",
            "content_backing_required",
            "content_digest",
            "content_size_bytes",
            "exact_read_ref",
            "exact_read_verified",
            "goal_id",
            "project_scope",
            "lifecycle_state",
            "material_ref",
            "observed_at",
            "owner_gate_required",
            "private_locations_captured",
            "proposal_id",
            "proposal_ref",
            "raw_content_captured",
            "schema_version",
            "source_authority_revision",
            "source_mutation_authorized",
            "source_ref",
            "source_revision",
            "store_id",
            "visibility",
        },
        required={
            "content_digest",
            "content_size_bytes",
            "exact_read_ref",
            "material_ref",
            "proposal_ref",
            "schema_version",
            "source_authority_revision",
            "source_ref",
            "source_revision",
            "store_id",
        },
    )
    if value.get("schema_version") != MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION:
        raise ValueError("candidate intake proposal has an unsupported schema_version")
    owner = _packet_owner(value)

    required_truth = {
        "owner_gate_required": True,
        "exact_read_verified": True,
        "content_backing_required": True,
        "source_mutation_authorized": False,
        "raw_content_captured": False,
        "private_locations_captured": False,
        "lifecycle_state": "candidate",
    }
    for field, expected in required_truth.items():
        if value.get(field) != expected:
            raise ValueError(f"candidate intake proposal has invalid {field}")
    return {
        **owner,
        "proposal_ref": compact_token(
            value["proposal_ref"],
            field="proposal.proposal_ref",
        ),
        "store_id": compact_token(value["store_id"], field="proposal.store_id"),
        "source_authority_revision": compact_token(
            value["source_authority_revision"],
            field="proposal.source_authority_revision",
        ),
        "material_ref": compact_token(
            value["material_ref"],
            field="proposal.material_ref",
        ),
        "source_ref": compact_token(value["source_ref"], field="proposal.source_ref"),
        "source_revision": compact_token(
            value["source_revision"],
            field="proposal.source_revision",
        ),
        "exact_read_ref": compact_token(
            value["exact_read_ref"],
            field="proposal.exact_read_ref",
        ),
        "content_digest": compact_token(
            value["content_digest"],
            field="proposal.content_digest",
        ),
        "content_size_bytes": positive_int(
            value["content_size_bytes"],
            field="proposal.content_size_bytes",
        ),
    }


def _validate_candidate(
    value: MaterialCandidateReadback,
    *,
    proposal: Mapping[str, Any],
    authority_revision: str,
) -> dict[str, Any]:
    expected = {
        "material_ref": proposal["material_ref"],
        "source_ref": proposal["source_ref"],
        "source_revision": proposal["source_revision"],
        "exact_read_ref": proposal["exact_read_ref"],
        "content_digest": proposal["content_digest"],
    }
    for field, expected_value in expected.items():
        if compact_token(
            getattr(value, field), field=f"candidate_readback.{field}"
        ) != (expected_value):
            raise ValueError(f"candidate readback {field} does not match proposal")
    if value.lifecycle_state != "candidate":
        raise ValueError("candidate readback lifecycle state does not match")
    if value.authority_revision != authority_revision:
        raise ValueError("candidate readback authority revision does not match")
    if (
        positive_int(
            value.content_size_bytes,
            field="candidate_readback.content_size_bytes",
        )
        != proposal["content_size_bytes"]
    ):
        raise ValueError("candidate readback content size does not match proposal")
    _verified(
        value.readback_verified,
        field="candidate_readback.readback_verified",
    )
    return {
        "candidate_record_ref": compact_token(
            value.candidate_record_ref,
            field="candidate_readback.candidate_record_ref",
        ),
        "content_backing_ref": compact_token(
            value.content_backing_ref,
            field="candidate_readback.content_backing_ref",
        ),
    }


def apply_material_candidate_intake(
    *,
    provider: MaterialCandidateIntakeProvider,
    provider_id: str,
    intake_proposal: Mapping[str, Any],
    content: bytes,
    owner_gate_ref: str,
    receipt_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Append one candidate through staged readback and authority-pointer CAS."""

    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if not content:
        raise ValueError("content must be non-empty")
    proposal = _proposal(intake_proposal)
    if len(content) != proposal["content_size_bytes"]:
        raise ValueError("content size does not match candidate intake proposal")
    if _content_digest(content) != proposal["content_digest"]:
        raise ValueError("content digest does not match candidate intake proposal")

    expected_provider = _provider_id(provider, expected=provider_id)
    store_id = proposal["store_id"]
    source_revision = proposal["source_authority_revision"]
    material_ref = proposal["material_ref"]
    timestamp = iso_timestamp(observed_at, field="observed_at")
    owner_gate = compact_token(owner_gate_ref, field="owner_gate_ref")

    verify_project_material_write(
        provider, owner=proposal, store_id=store_id, owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )

    before = _snapshot(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        observed_at=timestamp,
    )
    if before.authority_revision != source_revision:
        raise ValueError("candidate intake source revision CAS failed before staging")
    if (
        _readback(
            provider,
            provider_id=expected_provider,
            store_id=store_id,
            authority_revision=source_revision,
            material_ref=material_ref,
            observed_at=timestamp,
        )
        is not None
    ):
        raise ValueError("candidate intake material reference already exists")

    staged = provider.stage_candidate(
        store_id=store_id,
        proposal_ref=proposal["proposal_ref"],
        source_revision=source_revision,
        material_ref=material_ref,
        source_ref=proposal["source_ref"],
        source_material_revision=proposal["source_revision"],
        exact_read_ref=proposal["exact_read_ref"],
        content=content,
        owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )
    if not isinstance(staged, MaterialStagedCandidate):
        raise TypeError("candidate intake provider must return MaterialStagedCandidate")
    _identity(
        staged,
        provider_id=expected_provider,
        store_id=store_id,
        field="staged_candidate",
    )
    if staged.source_revision != source_revision:
        raise ValueError("staged candidate source revision does not match")
    target_revision = compact_token(
        staged.target_revision,
        field="staged_candidate.target_revision",
    )
    if target_revision == source_revision:
        raise ValueError("candidate intake target revision must differ")
    if staged.material_ref != material_ref:
        raise ValueError("staged candidate material reference does not match")
    if staged.content_digest != proposal["content_digest"]:
        raise ValueError("staged candidate content digest does not match")
    if staged.content_size_bytes != proposal["content_size_bytes"]:
        raise ValueError("staged candidate content size does not match")
    candidate_record_ref = compact_token(
        staged.candidate_record_ref,
        field="staged_candidate.candidate_record_ref",
    )
    content_backing_ref = compact_token(
        staged.content_backing_ref,
        field="staged_candidate.content_backing_ref",
    )
    _verified(
        staged.atomic_write_verified,
        field="staged_candidate.atomic_write_verified",
    )
    _verified(
        staged.readback_verified,
        field="staged_candidate.readback_verified",
    )

    staged_readback = _readback(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        authority_revision=target_revision,
        material_ref=material_ref,
        observed_at=timestamp,
    )
    if staged_readback is None:
        raise ValueError("staged candidate readback is missing")
    staged_refs = _validate_candidate(
        staged_readback,
        proposal=proposal,
        authority_revision=target_revision,
    )
    if staged_refs["candidate_record_ref"] != candidate_record_ref:
        raise ValueError("staged candidate record reference does not match")
    if staged_refs["content_backing_ref"] != content_backing_ref:
        raise ValueError("staged content backing reference does not match")

    current = _snapshot(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        observed_at=timestamp,
    )
    if current.authority_revision != source_revision:
        raise ValueError("candidate intake source revision CAS failed after staging")

    reconciliation = provider.reconcile_candidate_append(
        store_id=store_id,
        source_revision=source_revision,
        target_revision=target_revision,
        material_ref=material_ref,
        observed_at=timestamp,
    )
    if not isinstance(reconciliation, MaterialCandidateAppendReconciliation):
        raise TypeError(
            "candidate intake provider must return "
            "MaterialCandidateAppendReconciliation"
        )
    _identity(
        reconciliation,
        provider_id=expected_provider,
        store_id=store_id,
        field="append_reconciliation",
    )
    if reconciliation.source_revision != source_revision:
        raise ValueError("append reconciliation source revision does not match")
    if reconciliation.target_revision != target_revision:
        raise ValueError("append reconciliation target revision does not match")
    if reconciliation.material_ref != material_ref:
        raise ValueError("append reconciliation material reference does not match")
    before_count = nonnegative_int(
        reconciliation.before_item_count,
        field="append_reconciliation.before_item_count",
    )
    after_count = nonnegative_int(
        reconciliation.after_item_count,
        field="append_reconciliation.after_item_count",
    )
    if after_count != before_count + 1:
        raise ValueError("candidate intake must append exactly one record")
    _verified(
        reconciliation.stable_ids_verified,
        field="append_reconciliation.stable_ids_verified",
    )
    _verified(
        reconciliation.existing_records_unchanged,
        field="append_reconciliation.existing_records_unchanged",
    )
    if nonnegative_int(
        reconciliation.duplicate_material_count,
        field="append_reconciliation.duplicate_material_count",
    ):
        raise ValueError("candidate intake reconciliation contains duplicates")
    validation_ref = compact_token(
        reconciliation.validation_ref,
        field="append_reconciliation.validation_ref",
    )

    verify_project_material_write(
        provider, owner=proposal, store_id=store_id, owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )

    transition = provider.switch_authority(
        store_id=store_id,
        expected_revision=source_revision,
        target_revision=target_revision,
        owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )
    if not isinstance(transition, MaterialAuthorityTransition):
        raise TypeError(
            "candidate intake provider must return MaterialAuthorityTransition"
        )
    _identity(
        transition,
        provider_id=expected_provider,
        store_id=store_id,
        field="authority_transition",
    )
    if transition.before_revision != source_revision:
        raise ValueError("candidate intake transition source revision does not match")
    if transition.after_revision != target_revision:
        raise ValueError("candidate intake transition target revision does not match")
    _verified(
        transition.atomic_write_verified,
        field="authority_transition.atomic_write_verified",
    )
    _verified(
        transition.readback_verified,
        field="authority_transition.readback_verified",
    )

    after = _snapshot(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        observed_at=timestamp,
    )
    if after.authority_revision != target_revision:
        raise ValueError("candidate intake authority readback does not match")
    active_readback = _readback(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        authority_revision=target_revision,
        material_ref=material_ref,
        observed_at=timestamp,
    )
    if active_readback is None:
        raise ValueError("candidate intake authority readback is missing")
    _validate_candidate(
        active_readback,
        proposal=proposal,
        authority_revision=target_revision,
    )

    receipt: dict[str, Any] = {
        "schema_version": MATERIAL_CANDIDATE_INTAKE_APPLY_RECEIPT_SCHEMA_VERSION,
        **material_owner_fields(
            goal_id=proposal.get("goal_id"), project_scope=proposal.get("project_scope"),
        ),
        "receipt_id": compact_token(receipt_id, field="receipt_id"),
        "proposal_ref": proposal["proposal_ref"],
        "provider_id": expected_provider,
        "store_id": store_id,
        "material_ref": material_ref,
        "source_ref": proposal["source_ref"],
        "source_revision": proposal["source_revision"],
        "exact_read_ref": proposal["exact_read_ref"],
        "content_digest": proposal["content_digest"],
        "content_size_bytes": proposal["content_size_bytes"],
        "candidate_record_ref": candidate_record_ref,
        "content_backing_ref": content_backing_ref,
        "before_revision": source_revision,
        "after_revision": target_revision,
        "before_item_count": before_count,
        "after_item_count": after_count,
        "owner_gate_ref": owner_gate,
        "validation_ref": validation_ref,
        "authority_ref": compact_token(
            transition.authority_ref,
            field="authority_transition.authority_ref",
        ),
        "rollback_ref": compact_token(
            transition.rollback_ref,
            field="authority_transition.rollback_ref",
        ),
        "status": "applied",
        "lifecycle_state": "candidate",
        "source_revision_cas_verified": True,
        "content_backing_verified": True,
        "existing_records_unchanged": True,
        "atomic_write_verified": True,
        "readback_verified": True,
        "raw_content_captured": False,
        "private_locations_captured": False,
        "visibility": "public_safe",
        "observed_at": timestamp,
        "capability": capability_contract(packet_role="candidate_intake_apply_receipt", project_scoped="project_scope" in proposal),
    }
    receipt["receipt_ref"] = packet_ref("material-candidate-intake-apply", receipt)
    return receipt


def rollback_material_candidate_intake(
    *,
    provider: MaterialCandidateIntakeProvider,
    provider_id: str,
    apply_receipt: Mapping[str, Any],
    owner_gate_ref: str,
    receipt_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Restore the previous authority revision without deleting staged evidence."""

    if not isinstance(apply_receipt, Mapping):
        raise TypeError("apply_receipt must be an object")
    if (
        apply_receipt.get("schema_version")
        != MATERIAL_CANDIDATE_INTAKE_APPLY_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError(
            "candidate intake apply receipt has unsupported schema_version"
        )
    if apply_receipt.get("status") != "applied":
        raise ValueError("candidate intake apply receipt must be applied")

    expected_provider = _provider_id(provider, expected=provider_id)
    if apply_receipt.get("provider_id") != expected_provider:
        raise ValueError("candidate intake apply receipt provider does not match")
    store_id = compact_token(apply_receipt.get("store_id"), field="receipt.store_id")
    material_ref = compact_token(
        apply_receipt.get("material_ref"),
        field="receipt.material_ref",
    )
    source_revision = compact_token(
        apply_receipt.get("before_revision"),
        field="receipt.before_revision",
    )
    current_revision = compact_token(
        apply_receipt.get("after_revision"),
        field="receipt.after_revision",
    )
    timestamp = iso_timestamp(observed_at, field="observed_at")
    owner_gate = compact_token(owner_gate_ref, field="owner_gate_ref")

    owner = _packet_owner(apply_receipt)
    verify_project_material_write(
        provider, owner=owner, store_id=store_id, owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )

    current = _snapshot(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        observed_at=timestamp,
    )
    if current.authority_revision != current_revision:
        raise ValueError("candidate intake rollback authority revision CAS failed")

    verify_project_material_write(
        provider, owner=owner, store_id=store_id, owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )

    transition = provider.switch_authority(
        store_id=store_id,
        expected_revision=current_revision,
        target_revision=source_revision,
        owner_gate_ref=owner_gate,
        observed_at=timestamp,
    )
    if not isinstance(transition, MaterialAuthorityTransition):
        raise TypeError(
            "candidate intake provider must return MaterialAuthorityTransition"
        )
    _identity(
        transition,
        provider_id=expected_provider,
        store_id=store_id,
        field="rollback_transition",
    )
    if transition.before_revision != current_revision:
        raise ValueError("candidate intake rollback source revision does not match")
    if transition.after_revision != source_revision:
        raise ValueError("candidate intake rollback target revision does not match")
    _verified(
        transition.atomic_write_verified,
        field="rollback_transition.atomic_write_verified",
    )
    _verified(
        transition.readback_verified,
        field="rollback_transition.readback_verified",
    )

    restored = _snapshot(
        provider,
        provider_id=expected_provider,
        store_id=store_id,
        observed_at=timestamp,
    )
    if restored.authority_revision != source_revision:
        raise ValueError("candidate intake rollback readback does not match")
    if (
        _readback(
            provider,
            provider_id=expected_provider,
            store_id=store_id,
            authority_revision=source_revision,
            material_ref=material_ref,
            observed_at=timestamp,
        )
        is not None
    ):
        raise ValueError("candidate intake rollback did not restore source contents")

    receipt: dict[str, Any] = {
        "schema_version": MATERIAL_CANDIDATE_INTAKE_ROLLBACK_RECEIPT_SCHEMA_VERSION,
        **owner,
        "receipt_id": compact_token(receipt_id, field="receipt_id"),
        "apply_receipt_ref": compact_token(
            apply_receipt.get("receipt_ref"),
            field="receipt.receipt_ref",
        ),
        "provider_id": expected_provider,
        "store_id": store_id,
        "material_ref": material_ref,
        "before_revision": current_revision,
        "after_revision": source_revision,
        "owner_gate_ref": owner_gate,
        "authority_ref": compact_token(
            transition.authority_ref,
            field="rollback_transition.authority_ref",
        ),
        "rollback_ref": compact_token(
            transition.rollback_ref,
            field="rollback_transition.rollback_ref",
        ),
        "status": "rolled_back",
        "source_revision_cas_verified": True,
        "candidate_removed_from_authority": True,
        "staged_revision_retained": True,
        "atomic_write_verified": True,
        "readback_verified": True,
        "raw_content_captured": False,
        "private_locations_captured": False,
        "visibility": "public_safe",
        "observed_at": timestamp,
        "capability": capability_contract(
            packet_role="candidate_intake_rollback_receipt",
            project_scoped="project_scope" in owner,
        ),
    }
    receipt["receipt_ref"] = packet_ref(
        "material-candidate-intake-rollback",
        receipt,
    )
    return receipt
