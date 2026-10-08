"""Revise one existing candidate through the existing material source owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ._validation import (
    capability_contract,
    compact_token,
    iso_timestamp,
    nonnegative_int,
    packet_ref,
)
from .apply import MaterialAuthorityTransition
from .intake import (
    MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION,
    MaterialCandidateIntakeProvider,
    MaterialCandidateReadback,
    MaterialStagedCandidate,
    _content_digest,
    _identity,
    _packet_owner,
    _proposal,
    _provider_id,
    _readback,
    _snapshot,
    _validate_candidate,
    _verified,
    build_material_candidate_intake_proposal,
)
from .ownership import MaterialProjectScope, verify_project_material_write

MATERIAL_CANDIDATE_REVISION_PROPOSAL_SCHEMA_VERSION = (
    "material_candidate_revision_proposal_v0"
)
MATERIAL_CANDIDATE_REVISION_APPLY_RECEIPT_SCHEMA_VERSION = (
    "material_candidate_revision_apply_receipt_v0"
)
MATERIAL_CANDIDATE_REVISION_ROLLBACK_RECEIPT_SCHEMA_VERSION = (
    "material_candidate_revision_rollback_receipt_v0"
)


@dataclass(frozen=True)
class MaterialCandidateRevisionReconciliation:
    """Source proof: replace one record, retaining membership, ranks and history."""

    provider_id: str
    store_id: str
    source_revision: str
    target_revision: str
    material_ref: str
    before_item_count: int
    after_item_count: int
    validation_ref: str
    stable_ids_verified: bool = False
    other_records_unchanged: bool = False
    rankings_unchanged: bool = False
    lifecycle_unchanged: bool = False
    previous_content_retained: bool = False


class MaterialCandidateRevisionProvider(MaterialCandidateIntakeProvider, Protocol):
    """Optional extension of a material source; intake remains append-only."""

    def stage_candidate_revision(
        self,
        *,
        store_id: str,
        proposal_ref: str,
        source_revision: str,
        material_ref: str,
        expected_record_ref: str,
        candidate_record_ref: str,
        source_ref: str,
        source_material_revision: str,
        exact_read_ref: str,
        content: bytes,
        owner_gate_ref: str,
        observed_at: str,
    ) -> MaterialStagedCandidate: ...

    def reconcile_candidate_revision(
        self,
        *,
        store_id: str,
        source_revision: str,
        target_revision: str,
        material_ref: str,
        observed_at: str,
    ) -> MaterialCandidateRevisionReconciliation: ...


def build_material_candidate_revision_proposal(
    *,
    expected_record_ref: str,
    candidate_record_ref: str,
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
    """Bind the previous and replacement records to exact-read content evidence."""

    proposal = build_material_candidate_intake_proposal(
        goal_id=goal_id,
        project_scope=project_scope,
        proposal_id=proposal_id,
        store_id=store_id,
        source_authority_revision=source_authority_revision,
        material_ref=material_ref,
        source_ref=source_ref,
        source_revision=source_revision,
        exact_read_ref=exact_read_ref,
        content_digest=content_digest,
        content_size_bytes=content_size_bytes,
        observed_at=observed_at,
    )
    proposal.pop("proposal_ref")
    proposal.update(
        schema_version=MATERIAL_CANDIDATE_REVISION_PROPOSAL_SCHEMA_VERSION,
        expected_record_ref=compact_token(
            expected_record_ref, field="expected_record_ref"
        ),
        candidate_record_ref=compact_token(
            candidate_record_ref, field="candidate_record_ref"
        ),
        capability=capability_contract(
            packet_role="candidate_revision_proposal",
            project_scoped=project_scope is not None,
        ),
    )
    proposal["proposal_ref"] = packet_ref("material-candidate-revision", proposal)
    return proposal


def _revision_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        value.get("schema_version")
        != MATERIAL_CANDIDATE_REVISION_PROPOSAL_SCHEMA_VERSION
    ):
        raise ValueError("candidate revision proposal has unsupported schema_version")
    packet = dict(value)
    supplied_ref = packet.pop("proposal_ref", None)
    if supplied_ref != packet_ref("material-candidate-revision", packet):
        raise ValueError("candidate revision proposal digest does not match")
    previous = compact_token(
        packet.pop("expected_record_ref", None), field="expected_record_ref"
    )
    replacement = compact_token(
        packet.pop("candidate_record_ref", None), field="candidate_record_ref"
    )
    packet.update(
        schema_version=MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION,
        proposal_ref=supplied_ref,
    )
    return {
        **_proposal(packet),
        "expected_record_ref": previous,
        "candidate_record_ref": replacement,
    }


def _candidate(
    provider: MaterialCandidateIntakeProvider,
    provider_id: str,
    proposal: Mapping[str, Any],
    revision: str,
    timestamp: str,
) -> MaterialCandidateReadback:
    value = _readback(
        provider,
        provider_id=provider_id,
        store_id=proposal["store_id"],
        authority_revision=revision,
        material_ref=proposal["material_ref"],
        observed_at=timestamp,
    )
    if value is None:
        raise ValueError("candidate revision target is missing")
    if value.lifecycle_state != "candidate":
        raise ValueError("candidate revision cannot reactivate an archived material")
    _verified(value.readback_verified, field="candidate_readback.readback_verified")
    return value


def _switch(
    provider: MaterialCandidateIntakeProvider,
    provider_id: str,
    owner: Mapping[str, Any],
    before: str,
    after: str,
    gate: str,
    timestamp: str,
) -> MaterialAuthorityTransition:
    store = owner["store_id"]
    verify_project_material_write(
        provider,
        owner=owner,
        store_id=store,
        owner_gate_ref=gate,
        observed_at=timestamp,
    )
    current = _snapshot(
        provider, provider_id=provider_id, store_id=store, observed_at=timestamp
    )
    if current.authority_revision != before:
        raise ValueError("candidate revision authority CAS failed")
    transition = provider.switch_authority(
        store_id=store,
        expected_revision=before,
        target_revision=after,
        owner_gate_ref=gate,
        observed_at=timestamp,
    )
    if not isinstance(transition, MaterialAuthorityTransition):
        raise TypeError("candidate revision requires MaterialAuthorityTransition")
    _identity(
        transition,
        provider_id=provider_id,
        store_id=store,
        field="authority_transition",
    )
    if transition.before_revision != before or transition.after_revision != after:
        raise ValueError("candidate revision transition revisions do not match")
    for field in ("atomic_write_verified", "readback_verified"):
        _verified(getattr(transition, field), field=f"authority_transition.{field}")
    active = _snapshot(
        provider, provider_id=provider_id, store_id=store, observed_at=timestamp
    )
    if active.authority_revision != after:
        raise ValueError("candidate revision authority readback does not match")
    return transition


def _reconcile(
    provider: MaterialCandidateRevisionProvider,
    pid: str,
    owner: Mapping[str, Any],
    source: str,
    target: str,
    timestamp: str,
) -> tuple[int, str]:
    store = owner["store_id"]
    reconciliation = provider.reconcile_candidate_revision(
        store_id=store,
        source_revision=source,
        target_revision=target,
        material_ref=owner["material_ref"],
        observed_at=timestamp,
    )
    if not isinstance(reconciliation, MaterialCandidateRevisionReconciliation):
        raise TypeError(
            "candidate revision requires MaterialCandidateRevisionReconciliation"
        )
    _identity(
        reconciliation, provider_id=pid, store_id=store, field="revision_reconciliation"
    )
    for field, expected in (
        ("source_revision", source),
        ("target_revision", target),
        ("material_ref", owner["material_ref"]),
    ):
        if getattr(reconciliation, field) != expected:
            raise ValueError(
                f"candidate revision reconciliation {field} does not match"
            )
    count = nonnegative_int(reconciliation.before_item_count, field="before_item_count")
    if (
        nonnegative_int(reconciliation.after_item_count, field="after_item_count")
        != count
    ):
        raise ValueError("candidate revision must retain the record count")
    for field in (
        "stable_ids_verified",
        "other_records_unchanged",
        "rankings_unchanged",
        "lifecycle_unchanged",
        "previous_content_retained",
    ):
        _verified(
            getattr(reconciliation, field), field=f"revision_reconciliation.{field}"
        )
    return count, compact_token(reconciliation.validation_ref, field="validation_ref")


def apply_material_candidate_revision(
    *,
    provider: MaterialCandidateRevisionProvider,
    provider_id: str,
    revision_proposal: Mapping[str, Any],
    content: bytes,
    owner_gate_ref: str,
    receipt_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Replace exactly one candidate, leaving ranking to a separate settlement."""

    proposal = _revision_proposal(revision_proposal)
    if not isinstance(content, bytes) or not content:
        raise ValueError("candidate revision content must be non-empty bytes")
    if (
        len(content) != proposal["content_size_bytes"]
        or _content_digest(content) != proposal["content_digest"]
    ):
        raise ValueError("candidate revision content does not match proposal")
    pid = _provider_id(provider, expected=provider_id)
    timestamp = iso_timestamp(observed_at, field="observed_at")
    gate = compact_token(owner_gate_ref, field="owner_gate_ref")
    compact_token(receipt_id, field="receipt_id")
    source = proposal["source_authority_revision"]
    store = proposal["store_id"]
    verify_project_material_write(
        provider,
        owner=proposal,
        store_id=store,
        owner_gate_ref=gate,
        observed_at=timestamp,
    )
    if (
        _snapshot(
            provider, provider_id=pid, store_id=store, observed_at=timestamp
        ).authority_revision
        != source
    ):
        raise ValueError("candidate revision source CAS failed before staging")
    previous = _candidate(provider, pid, proposal, source, timestamp)
    if previous.candidate_record_ref != proposal["expected_record_ref"]:
        raise ValueError("candidate revision previous record CAS failed")
    staged = provider.stage_candidate_revision(
        store_id=store,
        proposal_ref=proposal["proposal_ref"],
        source_revision=source,
        material_ref=proposal["material_ref"],
        expected_record_ref=proposal["expected_record_ref"],
        candidate_record_ref=proposal["candidate_record_ref"],
        source_ref=proposal["source_ref"],
        source_material_revision=proposal["source_revision"],
        exact_read_ref=proposal["exact_read_ref"],
        content=content,
        owner_gate_ref=gate,
        observed_at=timestamp,
    )
    if not isinstance(staged, MaterialStagedCandidate):
        raise TypeError("candidate revision requires MaterialStagedCandidate")
    _identity(staged, provider_id=pid, store_id=store, field="staged_candidate")
    target = compact_token(staged.target_revision, field="target_revision")
    if staged.source_revision != source or target == source:
        raise ValueError("candidate revision staged revisions do not match")
    for field in (
        "material_ref",
        "candidate_record_ref",
        "content_digest",
        "content_size_bytes",
    ):
        if getattr(staged, field) != proposal[field]:
            raise ValueError(f"candidate revision staged {field} does not match")
    for field in ("atomic_write_verified", "readback_verified"):
        _verified(getattr(staged, field), field=f"staged_candidate.{field}")
    replacement = _candidate(provider, pid, proposal, target, timestamp)
    refs = _validate_candidate(
        replacement, proposal=proposal, authority_revision=target
    )
    if (
        refs["candidate_record_ref"] != proposal["candidate_record_ref"]
        or refs["content_backing_ref"] != staged.content_backing_ref
    ):
        raise ValueError("candidate revision staged record/backing does not match")
    count, validation = _reconcile(provider, pid, proposal, source, target, timestamp)
    if _candidate(provider, pid, proposal, source, timestamp) != previous:
        raise ValueError("candidate revision overwrote previous immutable content")
    transition = _switch(provider, pid, proposal, source, target, gate, timestamp)
    if _candidate(provider, pid, proposal, target, timestamp) != replacement:
        raise ValueError("candidate revision active record readback does not match")
    receipt = {
        "schema_version": MATERIAL_CANDIDATE_REVISION_APPLY_RECEIPT_SCHEMA_VERSION,
        "capability": capability_contract(
            packet_role="candidate_revision_apply_receipt",
            project_scoped="project_scope" in proposal,
        ),
        **_packet_owner(revision_proposal),
        "receipt_id": receipt_id,
        "proposal_ref": proposal["proposal_ref"],
        "provider_id": pid,
        "store_id": store,
        "material_ref": proposal["material_ref"],
        "candidate_record_ref": replacement.candidate_record_ref,
        "content_backing_ref": replacement.content_backing_ref,
        "content_digest": replacement.content_digest,
        "before_revision": source,
        "after_revision": target,
        "item_count": count,
        "previous_record": {
            field: getattr(previous, field)
            for field in (
                "material_ref",
                "source_ref",
                "source_revision",
                "exact_read_ref",
                "content_digest",
                "content_size_bytes",
                "candidate_record_ref",
                "content_backing_ref",
            )
        },
        "owner_gate_ref": gate,
        "validation_ref": validation,
        "authority_ref": compact_token(transition.authority_ref, field="authority_ref"),
        "rollback_ref": compact_token(transition.rollback_ref, field="rollback_ref"),
        "status": "applied",
        "rankings_unchanged": True,
        "other_records_unchanged": True,
        "previous_content_retained": True,
        "source_revision_cas_verified": True,
        "readback_verified": True,
        "raw_content_captured": False,
        "private_locations_captured": False,
        "visibility": "public_safe",
        "observed_at": timestamp,
    }
    receipt["receipt_ref"] = packet_ref("material-candidate-revision-apply", receipt)
    return receipt


def rollback_material_candidate_revision(
    *,
    provider: MaterialCandidateRevisionProvider,
    provider_id: str,
    apply_receipt: Mapping[str, Any],
    owner_gate_ref: str,
    receipt_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Restore the previous candidate, refusing rollback across later writes."""

    packet = dict(apply_receipt)
    supplied_ref = packet.pop("receipt_ref", None)
    if (
        packet.get("schema_version")
        != MATERIAL_CANDIDATE_REVISION_APPLY_RECEIPT_SCHEMA_VERSION
        or packet.get("status") != "applied"
        or supplied_ref != packet_ref("material-candidate-revision-apply", packet)
    ):
        raise ValueError(
            "candidate revision rollback requires an intact applied receipt"
        )
    pid = _provider_id(provider, expected=provider_id)
    if packet.get("provider_id") != pid:
        raise ValueError("candidate revision receipt provider does not match")
    owner = _packet_owner(packet)
    timestamp = iso_timestamp(observed_at, field="observed_at")
    gate = compact_token(owner_gate_ref, field="owner_gate_ref")
    compact_token(receipt_id, field="receipt_id")
    verify_project_material_write(
        provider,
        owner=owner,
        store_id=packet["store_id"],
        owner_gate_ref=gate,
        observed_at=timestamp,
    )
    previous = packet["previous_record"]
    target = packet["before_revision"]
    old = _candidate(provider, pid, packet, target, timestamp)
    refs = _validate_candidate(old, proposal=previous, authority_revision=target)
    if any(refs[field] != previous[field] for field in refs):
        raise ValueError("candidate revision rollback previous record does not match")
    _reconcile(provider, pid, packet, target, packet["after_revision"], timestamp)
    transition = _switch(
        provider, pid, packet, packet["after_revision"], target, gate, timestamp
    )
    if _candidate(provider, pid, packet, target, timestamp) != old:
        raise ValueError("candidate revision rollback readback does not match")
    result = {
        "schema_version": MATERIAL_CANDIDATE_REVISION_ROLLBACK_RECEIPT_SCHEMA_VERSION,
        "capability": capability_contract(
            packet_role="candidate_revision_rollback_receipt",
            project_scoped="project_scope" in packet,
        ),
        **_packet_owner(packet),
        "receipt_id": receipt_id,
        "apply_receipt_ref": supplied_ref,
        "provider_id": pid,
        "store_id": packet["store_id"],
        "material_ref": packet["material_ref"],
        "before_revision": packet["after_revision"],
        "after_revision": target,
        "owner_gate_ref": gate,
        "rollback_ref": transition.rollback_ref,
        "status": "rolled_back",
        "previous_content_restored": True,
        "staged_revision_retained": True,
        "readback_verified": True,
        "raw_content_captured": False,
        "private_locations_captured": False,
        "visibility": "public_safe",
        "observed_at": timestamp,
    }
    result["receipt_ref"] = packet_ref("material-candidate-revision-rollback", result)
    return result
