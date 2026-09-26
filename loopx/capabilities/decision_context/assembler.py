"""Deterministic authority rebase and advisory recall assembly."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..context_providers.base import (
    ContextProvider,
    ContextProviderItem,
    ContextProviderRetrieval,
    canonical_context_matches,
    opaque_provider_ref,
)
from .freshness import build_source_freshness_report, source_freshness_row
from .packets import build_decision_evidence_packet
from .sources import (
    DecisionSourceExactRead,
    DecisionSourceProvider,
    DecisionSourceScan,
    DecisionSourceSpec,
    build_decision_source_item_refs,
    build_decision_source_manifest,
)

DECISION_CONTEXT_ASSEMBLY_SCHEMA_VERSION = "decision_context_assembly_v0"
DECISION_CONTEXT_EPHEMERAL_RECALL_SCHEMA_VERSION = (
    "decision_context_ephemeral_recall_v0"
)
DECISION_CURSOR_CHECKPOINT_SCHEMA_VERSION = "decision_cursor_checkpoint_v0"
DECISION_SOURCE_COVERAGE_SCHEMA_VERSION = "decision_source_coverage_v0"
DECISION_SEMANTIC_REBASE_RECEIPT_SCHEMA_VERSION = (
    "decision_semantic_rebase_receipt_v0"
)


def _packet_ref(prefix: str, packet: Mapping[str, Any]) -> str:
    payload = json.dumps(
        packet,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _timestamp(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _positive_number(value: float, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be a positive number") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return number


@dataclass(frozen=True)
class DecisionAuthorityEvidence:
    """One exact-read authority item with public-safe linkage refs."""

    source_id: str
    source_ref: str
    source_revision: str
    observed_at: str
    resource_ref: str = field(repr=False)
    revision: str = field(repr=False)
    content: str = field(repr=False)


@dataclass(frozen=True)
class DecisionRecallEvidence:
    """One advisory recall item whose content remains transient."""

    provider_ref: str
    summary: str
    score: float | None
    resource_ref: str = field(repr=False)
    content: str = field(repr=False)


@dataclass(frozen=True)
class DecisionEvidenceRecords:
    """Domain rebase result consumed by the deterministic assembler."""

    changed_facts: tuple[Mapping[str, Any], ...] = ()
    recalled_claims: tuple[Mapping[str, Any], ...] = ()
    stale_or_rejected_claims: tuple[Mapping[str, Any], ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    semantic_no_change: bool = False


@dataclass(frozen=True)
class DecisionEvidenceCollection:
    """Transient collection exposed to a domain-specific rebase callback."""

    authority: tuple[DecisionAuthorityEvidence, ...]
    recall: tuple[DecisionRecallEvidence, ...]
    source_scan_receipts: tuple[Mapping[str, Any], ...]
    context_retrieval_receipt: Mapping[str, Any] | None


DecisionEvidenceRebaser = Callable[
    [DecisionEvidenceCollection],
    DecisionEvidenceRecords,
]


@dataclass(frozen=True)
class DecisionContextAssembly:
    """Public-safe assembly plus private proposed cursor updates."""

    packet: Mapping[str, Any]
    proposed_cursors: Mapping[str, str] = field(repr=False)
    expected_cursors: Mapping[str, str | None] = field(
        default_factory=dict,
        repr=False,
    )
    profile_digest: str | None = field(default=None, repr=False)
    runtime_bound_provider_ids: tuple[str, ...] = field(
        default_factory=tuple,
        repr=False,
    )

    def public_packet(self) -> dict[str, Any]:
        return dict(self.packet)


@dataclass(frozen=True)
class _CollectedSource:
    source: DecisionSourceSpec
    scan: DecisionSourceScan
    exact_reads: tuple[DecisionSourceExactRead, ...]
    exact_read_failed: bool
    receipt: Mapping[str, Any]


def _source_coverage(
    collected_sources: Sequence[_CollectedSource],
) -> dict[str, Any]:
    """Project whether every P0 source was scanned and exactly read when needed."""

    priority_rows: list[dict[str, Any]] = []
    incomplete_required_source_ids: list[str] = []
    for priority in ("p0", "p1", "p2"):
        sources = [
            collected
            for collected in collected_sources
            if collected.source.priority == priority
        ]
        status_counts = {
            status: sum(
                1 for collected in sources if collected.scan.status == status
            )
            for status in ("completed", "no_change", "failed", "unavailable")
        }
        exact_read_incomplete_count = sum(
            1 for collected in sources if collected.exact_read_failed
        )
        complete_count = sum(
            1
            for collected in sources
            if collected.scan.status in {"completed", "no_change"}
            and not collected.exact_read_failed
        )
        priority_rows.append(
            {
                "priority": priority,
                "source_count": len(sources),
                "complete_count": complete_count,
                "exact_read_incomplete_count": exact_read_incomplete_count,
                "status_counts": status_counts,
            }
        )
        if priority == "p0":
            incomplete_required_source_ids.extend(
                collected.source.source_id
                for collected in sources
                if collected.scan.status not in {"completed", "no_change"}
                or collected.exact_read_failed
            )

    incomplete_required_source_ids.sort()
    return {
        "schema_version": DECISION_SOURCE_COVERAGE_SCHEMA_VERSION,
        "required_priority": "p0",
        "status": (
            "complete" if not incomplete_required_source_ids else "incomplete"
        ),
        "complete": not incomplete_required_source_ids,
        "incomplete_required_source_ids": incomplete_required_source_ids,
        "priority_rows": priority_rows,
        "fail_open_preserved": True,
        "raw_context_captured": False,
        "private_locators_captured": False,
    }


def _unavailable_source_scan(
    *,
    source: DecisionSourceSpec,
    observed_at: str,
    reason_code: str,
) -> DecisionSourceScan:
    return DecisionSourceScan(
        provider_id=source.provider_id,
        source_id=source.source_id,
        status="unavailable",
        observed_at=observed_at,
        requested_limit=source.max_changes,
        reason_code=reason_code,
    )


def _unavailable_context_retrieval(
    *,
    provider: str,
    namespace: str,
    observed_at: str,
    query_summary: str,
    requested_limit: int,
    reason_code: str,
) -> ContextProviderRetrieval:
    return ContextProviderRetrieval(
        provider=provider,
        namespace=namespace,
        status="unavailable",
        query_summary=query_summary,
        observed_at=observed_at,
        search_performed=False,
        read_performed=False,
        reason_code=reason_code,
        requested_limit=requested_limit,
    )


def _collect_source(
    *,
    source: DecisionSourceSpec,
    provider: DecisionSourceProvider | None,
    cursor: str | None,
    before: str,
    timeout_seconds: float,
    observed_at: str,
) -> tuple[
    DecisionSourceScan,
    tuple[DecisionSourceExactRead, ...],
    bool,
]:
    if provider is None:
        return (
            _unavailable_source_scan(
                source=source,
                observed_at=observed_at,
                reason_code="provider_not_configured",
            ),
            (),
            False,
        )
    try:
        scan = provider.scan(
            source=source,
            after_cursor=cursor,
            before=before,
            limit=source.max_changes,
            timeout_seconds=timeout_seconds,
            observed_at=observed_at,
        )
    except Exception:
        return (
            _unavailable_source_scan(
                source=source,
                observed_at=observed_at,
                reason_code="provider_scan_failed",
            ),
            (),
            False,
        )
    if not isinstance(scan, DecisionSourceScan):
        return (
            _unavailable_source_scan(
                source=source,
                observed_at=observed_at,
                reason_code="provider_contract_invalid",
            ),
            (),
            False,
        )

    exact_reads: list[DecisionSourceExactRead] = []
    exact_read_failed = False
    if scan.status == "completed" and source.exact_read_policy != "never":
        for item in scan.items:
            try:
                exact_read = provider.exact_read(
                    source=source,
                    item=item,
                    timeout_seconds=timeout_seconds,
                    observed_at=observed_at,
                )
                if not isinstance(exact_read, DecisionSourceExactRead):
                    raise TypeError("exact_read must return DecisionSourceExactRead")
                if exact_read.verified:
                    exact_reads.append(exact_read)
                else:
                    exact_read_failed = True
            except Exception:
                exact_read_failed = True
    return scan, tuple(exact_reads), exact_read_failed


def collect_context_recall(
    *,
    provider: ContextProvider | None,
    namespace: str,
    scope_ref: str,
    query: str,
    query_summary: str,
    max_results: int,
    timeout_seconds: float,
    observed_at: str,
) -> ContextProviderRetrieval | None:
    if provider is None:
        return None
    try:
        retrieval = provider.retrieve(
            namespace=namespace,
            scope_ref=scope_ref,
            query=query,
            query_summary=query_summary,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
            observed_at=observed_at,
        )
    except Exception:
        return _unavailable_context_retrieval(
            provider="context-provider",
            namespace=namespace,
            observed_at=observed_at,
            query_summary=query_summary,
            requested_limit=max_results,
            reason_code="provider_retrieval_failed",
        )
    provider_id = str(getattr(provider, "provider_id", ""))
    contract_valid = (
        isinstance(retrieval, ContextProviderRetrieval)
        and retrieval.provider == provider_id
        and retrieval.namespace == namespace
        and len(retrieval.items) <= max_results
        and all(isinstance(item, ContextProviderItem) for item in retrieval.items)
    )
    if not contract_valid:
        return _unavailable_context_retrieval(
            provider="context-provider",
            namespace=namespace,
            observed_at=observed_at,
            query_summary=query_summary,
            requested_limit=max_results,
            reason_code="provider_contract_invalid",
        )
    try:
        retrieval.public_packet()
    except Exception:
        return _unavailable_context_retrieval(
            provider="context-provider",
            namespace=namespace,
            observed_at=observed_at,
            query_summary=query_summary,
            requested_limit=max_results,
            reason_code="provider_contract_invalid",
        )
    return retrieval


def _freshness(
    *,
    item_observed_at: str,
    assembly_observed_at: datetime,
    freshness_seconds: int,
) -> str:
    item_time = _timestamp(item_observed_at, field_name="item.observed_at")
    age_seconds = max(0.0, (assembly_observed_at - item_time).total_seconds())
    return "current" if age_seconds <= freshness_seconds else "stale"


def _verified_recalled_claims(
    *,
    claims: Sequence[Mapping[str, Any]],
    authority: Sequence[DecisionAuthorityEvidence],
    recall: Sequence[DecisionRecallEvidence],
) -> list[dict[str, Any]]:
    authority_by_ref = {
        (item.source_ref, item.source_revision): item for item in authority
    }
    recall_by_ref = {item.provider_ref: item for item in recall}
    verified: list[dict[str, Any]] = []
    for claim in claims:
        provider_ref = str(claim.get("provider_ref") or "")
        source_ref = str(claim.get("source_ref") or "")
        source_revision = str(claim.get("source_revision") or "")
        recalled_item = recall_by_ref.get(provider_ref)
        authority_item = authority_by_ref.get((source_ref, source_revision))
        if (
            recalled_item is None
            or authority_item is None
            or not canonical_context_matches(
                recalled_item.content,
                authority_item.content,
            )
        ):
            raise ValueError(
                "recalled_claims must match an exact-read authority revision; "
                "reject stale or conflicting recall instead"
            )
        record = dict(claim)
        record["exact_read_verified"] = True
        verified.append(record)
    return verified


def _verified_changed_facts(
    *,
    facts: Sequence[Mapping[str, Any]],
    authority: Sequence[DecisionAuthorityEvidence],
) -> list[dict[str, Any]]:
    authority_keys = {(item.source_ref, item.source_revision) for item in authority}
    verified: list[dict[str, Any]] = []
    for fact in facts:
        source_ref = str(fact.get("source_ref") or "")
        source_revision = str(fact.get("source_revision") or "")
        if not source_revision or (source_ref, source_revision) not in authority_keys:
            raise ValueError(
                "changed_facts must reference an exact-read authority revision"
            )
        verified.append(dict(fact))
    return verified


def _assembly_source_freshness(
    *,
    collected_sources: Sequence[_CollectedSource],
    coverage_sources: Sequence[DecisionSourceSpec],
    assembly_time: datetime,
) -> dict[str, Any]:
    collected_by_id = {
        collected.source.source_id: collected for collected in collected_sources
    }
    rows = []
    for source in coverage_sources:
        collected = collected_by_id.get(source.source_id)
        if collected is None:
            rows.append(
                source_freshness_row(
                    source=source,
                    observed_at=assembly_time,
                    last_read_at=None,
                    last_attempt_status=None,
                    scanned=False,
                )
            )
            continue
        attempt = (
            "exact_read_failed" if collected.exact_read_failed else collected.scan.status
        )
        rows.append(
            source_freshness_row(
                source=source,
                observed_at=assembly_time,
                last_read_at=(
                    assembly_time.isoformat()
                    if attempt in {"completed", "no_change"}
                    else None
                ),
                last_attempt_status=attempt,
            )
        )
    return build_source_freshness_report(observed_at=assembly_time, rows=rows)


def _accounted_authority(
    evidence: Mapping[str, Any],
) -> tuple[set[tuple[str, str]], set[str]]:
    accounted_pairs: set[tuple[str, str]] = set()
    for field_name in (
        "changed_facts",
        "recalled_claims",
        "stale_or_rejected_claims",
    ):
        for record in evidence[field_name]:
            source_ref = str(record.get("source_ref") or "")
            source_revision = str(record.get("source_revision") or "")
            if source_ref and source_revision:
                accounted_pairs.add((source_ref, source_revision))

    conflict_refs = {
        str(source_ref)
        for conflict in evidence["conflicts"]
        for source_ref in conflict["source_refs"]
    }
    return accounted_pairs, conflict_refs


def _checkpoint_disposition(
    *,
    collected: _CollectedSource,
    accounted_pairs: set[tuple[str, str]],
    conflict_refs: set[str],
    semantic_no_change: bool,
) -> tuple[bool, str]:
    scan = collected.scan
    if scan.cursor_after is None:
        return False, "cursor_after_missing"
    if scan.status == "no_change":
        return True, "no_change"
    if scan.status != "completed":
        return False, "scan_incomplete"
    if not scan.items:
        return True, "no_change"
    if collected.source.exact_read_policy == "never":
        return False, "exact_read_disabled"
    if collected.exact_read_failed or len(collected.exact_reads) != len(scan.items):
        return False, "exact_read_incomplete"
    if semantic_no_change:
        return True, "semantic_no_change"

    changed_refs = []
    for item in scan.items:
        refs = build_decision_source_item_refs(
            provider_id=collected.source.provider_id,
            source_id=collected.source.source_id,
            item=item,
        )
        changed_refs.append((refs["source_ref"], refs["source_revision"]))
    if any(
        pair not in accounted_pairs and pair[0] not in conflict_refs
        for pair in changed_refs
    ):
        return False, "rebase_incomplete"
    return True, "validated_rebase"


def _cursor_checkpoint(
    *,
    goal_id: str,
    decision_id: str,
    observed_at: str,
    evidence_packet_ref: str,
    source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "schema_version": DECISION_CURSOR_CHECKPOINT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "decision_id": decision_id,
        "observed_at": observed_at,
        "evidence_packet_ref": evidence_packet_ref,
        "visibility": "public_safe",
        "sources": sorted(
            [dict(row) for row in source_rows],
            key=lambda row: str(row["source_id"]),
        ),
        "apply_requires_validated_decision_writeback": True,
        "applied": False,
        "raw_cursors_captured": False,
        "external_writes_performed": False,
    }
    packet["checkpoint_ref"] = _packet_ref("decision-cursor-checkpoint", packet)
    return packet


def assemble_decision_evidence(
    *,
    goal_id: str,
    decision_id: str,
    observed_at: str,
    before: str,
    sources: Sequence[DecisionSourceSpec],
    source_providers: Mapping[str, DecisionSourceProvider],
    cursors: Mapping[str, str | None],
    rebase: DecisionEvidenceRebaser,
    context_provider: ContextProvider | None = None,
    context_namespace: str = "decision-context",
    context_scope_ref: str = "goal",
    recall_query: str = "current decision evidence",
    recall_query_summary: str = "current decision evidence",
    recall_limit: int = 5,
    timeout_seconds: float = 10.0,
    coverage_sources: Sequence[DecisionSourceSpec] | None = None,
) -> DecisionContextAssembly:
    """Collect, rebase, and assemble one public-safe decision evidence packet.

    Cursor values are returned only as private proposals. A caller may persist
    them after a reviewed proposal or explicit semantic no-change result has
    been written back and validated. Later outcome observation is separate.
    ``coverage_sources`` lists every enabled source the freshness projection
    must account for; enabled sources outside this scan are reported as
    ``not_scanned`` instead of being silently omitted.
    """

    assembly_time = _timestamp(observed_at, field_name="observed_at")
    _timestamp(before, field_name="before")
    timeout_seconds = _positive_number(
        timeout_seconds,
        field_name="timeout_seconds",
    )
    if (
        isinstance(recall_limit, bool)
        or not isinstance(recall_limit, int)
        or recall_limit <= 0
    ):
        raise ValueError("recall_limit must be a positive integer")

    enabled_sources = sorted(
        (source for source in sources if source.enabled),
        key=lambda source: source.source_id,
    )
    manifest = build_decision_source_manifest(
        goal_id=goal_id,
        observed_at=observed_at,
        sources=enabled_sources,
    )

    authority_items: list[DecisionAuthorityEvidence] = []
    source_scan_receipts: list[Mapping[str, Any]] = []
    source_revisions: list[dict[str, Any]] = []
    provider_health: list[dict[str, Any]] = []
    collected_sources: list[_CollectedSource] = []

    for source in enabled_sources:
        provider = source_providers.get(source.provider_id)
        scan, exact_reads, exact_read_failed = _collect_source(
            source=source,
            provider=provider,
            cursor=cursors.get(source.source_id),
            before=before,
            timeout_seconds=timeout_seconds,
            observed_at=observed_at,
        )
        try:
            if (
                scan.provider_id != source.provider_id
                or scan.source_id != source.source_id
            ):
                raise ValueError("provider scan identity does not match source")
            receipt = scan.public_receipt(exact_reads=exact_reads)
        except (TypeError, ValueError):
            scan = _unavailable_source_scan(
                source=source,
                observed_at=observed_at,
                reason_code="provider_contract_invalid",
            )
            exact_reads = ()
            exact_read_failed = False
            receipt = scan.public_receipt()
        source_scan_receipts.append(receipt)
        collected_sources.append(
            _CollectedSource(
                source=source,
                scan=scan,
                exact_reads=exact_reads,
                exact_read_failed=exact_read_failed,
                receipt=receipt,
            )
        )

        exact_by_key = {
            (item.resource_ref, item.revision): item for item in exact_reads
        }
        for item in scan.items:
            refs = build_decision_source_item_refs(
                provider_id=source.provider_id,
                source_id=source.source_id,
                item=item,
            )
            freshness = _freshness(
                item_observed_at=item.observed_at,
                assembly_observed_at=assembly_time,
                freshness_seconds=source.freshness_seconds,
            )
            source_revisions.append(
                {
                    "source_ref": refs["source_ref"],
                    "revision": refs["source_revision"],
                    "observed_at": item.observed_at,
                    "freshness": freshness,
                }
            )
            exact_read = exact_by_key.get((item.resource_ref, item.revision))
            if exact_read is not None:
                authority_items.append(
                    DecisionAuthorityEvidence(
                        source_id=source.source_id,
                        source_ref=refs["source_ref"],
                        source_revision=refs["source_revision"],
                        observed_at=item.observed_at,
                        resource_ref=item.resource_ref,
                        revision=item.revision,
                        content=exact_read.content,
                    )
                )

        provider_health.append(
            {
                "provider": source.provider_id,
                "status": (
                    "healthy"
                    if scan.status in {"completed", "no_change"}
                    and not exact_read_failed
                    else "degraded"
                    if scan.status == "completed"
                    else "unavailable"
                ),
                "observed_at": observed_at,
                "reason_code": scan.reason_code
                or ("exact_read_failed" if exact_read_failed else None),
                "fail_open": True,
            }
        )

    retrieval = collect_context_recall(
        provider=context_provider,
        namespace=context_namespace,
        scope_ref=context_scope_ref,
        query=recall_query,
        query_summary=recall_query_summary,
        max_results=recall_limit,
        timeout_seconds=timeout_seconds,
        observed_at=observed_at,
    )
    recall_items: list[DecisionRecallEvidence] = []
    retrieval_receipt: Mapping[str, Any] | None = None
    if retrieval is not None:
        retrieval_receipt = retrieval.public_packet()
        for retrieved_item in retrieval.items:
            recall_items.append(
                DecisionRecallEvidence(
                    provider_ref=opaque_provider_ref(
                        provider=retrieval.provider,
                        namespace=retrieval.namespace,
                        resource_ref=retrieved_item.resource_ref,
                    ),
                    summary=retrieved_item.summary,
                    score=retrieved_item.score,
                    resource_ref=retrieved_item.resource_ref,
                    content=retrieved_item.content,
                )
            )
        provider_health.append(
            {
                "provider": retrieval.provider,
                "status": "healthy"
                if retrieval.status == "completed" and retrieval.read_performed
                else "degraded"
                if retrieval.status == "completed"
                else "unavailable",
                "observed_at": observed_at,
                "reason_code": retrieval.reason_code,
                "fail_open": True,
            }
        )

    collection = DecisionEvidenceCollection(
        authority=tuple(authority_items),
        recall=tuple(recall_items),
        source_scan_receipts=tuple(source_scan_receipts),
        context_retrieval_receipt=retrieval_receipt,
    )
    records = rebase(collection)
    if not isinstance(records, DecisionEvidenceRecords):
        raise TypeError("rebase must return DecisionEvidenceRecords")
    if not isinstance(records.semantic_no_change, bool):
        raise TypeError("semantic_no_change must be a boolean")
    if records.semantic_no_change and any(
        (
            records.changed_facts,
            records.recalled_claims,
            records.stale_or_rejected_claims,
            records.conflicts,
        )
    ):
        raise ValueError(
            "semantic_no_change cannot accompany material evidence records"
        )

    verified_facts = _verified_changed_facts(
        facts=records.changed_facts,
        authority=authority_items,
    )
    verified_claims = _verified_recalled_claims(
        claims=records.recalled_claims,
        authority=authority_items,
        recall=recall_items,
    )
    evidence = build_decision_evidence_packet(
        goal_id=goal_id,
        decision_id=decision_id,
        observed_at=observed_at,
        changed_facts=verified_facts,
        recalled_claims=verified_claims,
        stale_or_rejected_claims=records.stale_or_rejected_claims,
        conflicts=records.conflicts,
        source_revisions=source_revisions,
        provider_health=provider_health,
    )
    accounted_pairs, conflict_refs = _accounted_authority(evidence)
    cursor_rows: list[dict[str, Any]] = []
    proposed_cursors: dict[str, str] = {}
    for collected in collected_sources:
        checkpoint_ready, reason_code = _checkpoint_disposition(
            collected=collected,
            accounted_pairs=accounted_pairs,
            conflict_refs=conflict_refs,
            semantic_no_change=records.semantic_no_change,
        )
        if checkpoint_ready and collected.scan.cursor_after is not None:
            proposed_cursors[collected.source.source_id] = collected.scan.cursor_after
        cursor_rows.append(
            {
                "source_id": collected.source.source_id,
                "scan_receipt_ref": collected.receipt["receipt_ref"],
                "cursor_before_ref": collected.receipt["cursor_before_ref"],
                "cursor_after_ref": collected.receipt["cursor_after_ref"],
                "disposition": (
                    "ready_after_writeback" if checkpoint_ready else "preserve"
                ),
                "reason_code": reason_code,
            }
        )

    checkpoint = _cursor_checkpoint(
        goal_id=str(evidence["goal_id"]),
        decision_id=str(evidence["decision_id"]),
        observed_at=observed_at,
        evidence_packet_ref=str(evidence["packet_ref"]),
        source_rows=cursor_rows,
    )
    packet: dict[str, Any] = {
        "schema_version": DECISION_CONTEXT_ASSEMBLY_SCHEMA_VERSION,
        "goal_id": evidence["goal_id"],
        "decision_id": evidence["decision_id"],
        "observed_at": evidence["observed_at"],
        "visibility": "public_safe",
        "source_manifest": manifest,
        "source_scan_receipts": source_scan_receipts,
        "source_coverage": _source_coverage(collected_sources),
        "source_freshness": _assembly_source_freshness(
            collected_sources=collected_sources,
            coverage_sources=sorted(
                {
                    source.source_id: source
                    for source in (*(coverage_sources or ()), *enabled_sources)
                    if source.enabled
                }.values(),
                key=lambda source: source.source_id,
            ),
            assembly_time=assembly_time,
        ),
        "context_retrieval_receipt": retrieval_receipt,
        "evidence_packet": evidence,
        "semantic_rebase": {
            "schema_version": DECISION_SEMANTIC_REBASE_RECEIPT_SCHEMA_VERSION,
            "status": (
                "no_material_change"
                if records.semantic_no_change
                else "material_change"
                if any(
                    (
                        records.changed_facts,
                        records.recalled_claims,
                        records.stale_or_rejected_claims,
                        records.conflicts,
                    )
                )
                else "incomplete"
            ),
            "quiet_noop_allowed": records.semantic_no_change,
            "raw_context_captured": False,
        },
        "cursor_checkpoint": checkpoint,
        "provider_fail_open": True,
        "raw_context_captured": False,
        "raw_provider_payload_captured": False,
        "private_locators_captured": False,
        "private_cursors_captured": False,
        "external_writes_performed": False,
    }
    packet["assembly_ref"] = _packet_ref("decision-context-assembly", packet)
    return DecisionContextAssembly(
        packet=packet,
        proposed_cursors=proposed_cursors,
        expected_cursors={
            source.source_id: cursors.get(source.source_id)
            for source in enabled_sources
        },
    )
