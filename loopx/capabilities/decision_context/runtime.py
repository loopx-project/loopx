"""Thin profile-to-evidence orchestration for Decision Context."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..context_providers import build_context_provider
from ..context_providers.base import ContextProvider
from ...control_plane.runtime.public_safety import public_safe_compact_text
from .extension_provider import (
    EXTENSION_CONTEXT_PROVIDER_ID,
    build_extension_context_provider,
)
from .assembler import (
    DecisionContextAssembly,
    DECISION_CONTEXT_EPHEMERAL_RECALL_SCHEMA_VERSION,
    DecisionEvidenceRebaser,
    DecisionEvidenceRecords,
    assemble_decision_evidence,
    collect_context_recall,
)
from .private_state import load_private_decision_cursors, private_file_digest
from .profile import (
    DecisionContextProfile,
    resolve_decision_context_activation,
)
from .providers import build_decision_source_provider
from .sources import DecisionSourceProvider, DecisionSourceScan, DecisionSourceSpec


_DECISION_EVIDENCE_RECORD_FIELDS = {
    "changed_facts",
    "recalled_claims",
    "stale_or_rejected_claims",
    "conflicts",
    "semantic_no_change",
}


@dataclass(frozen=True)
class _TransientRecallRequest:
    scope_ref: str
    query: str
    query_summary: str
    max_results: int
    timeout_seconds: float


def decision_evidence_records_from_mapping(
    value: Mapping[str, Any],
) -> DecisionEvidenceRecords:
    """Load a strict domain rebase result without trusting private raw content."""

    if not isinstance(value, Mapping):
        raise TypeError("decision evidence records must be an object")
    unexpected = sorted(set(value) - _DECISION_EVIDENCE_RECORD_FIELDS)
    if unexpected:
        raise ValueError(
            "decision evidence records contain unsupported fields: "
            + ", ".join(unexpected)
        )

    def records(field: str) -> tuple[Mapping[str, Any], ...]:
        raw = value.get(field, [])
        if isinstance(raw, (str, bytes)) or not isinstance(raw, list):
            raise TypeError(f"{field} must be a list of objects")
        if any(not isinstance(item, Mapping) for item in raw):
            raise TypeError(f"{field} must be a list of objects")
        return tuple(dict(item) for item in raw)

    semantic_no_change = value.get("semantic_no_change", False)
    if not isinstance(semantic_no_change, bool):
        raise TypeError("semantic_no_change must be a boolean")
    return DecisionEvidenceRecords(
        changed_facts=records("changed_facts"),
        recalled_claims=records("recalled_claims"),
        stale_or_rejected_claims=records("stale_or_rejected_claims"),
        conflicts=records("conflicts"),
        semantic_no_change=semantic_no_change,
    )


class _UnavailableContextProvider:
    """Preserve a public fail-open receipt when provider construction fails."""

    provider_id = "context-provider"

    def retrieve(self, **_: Any) -> Any:
        raise RuntimeError("context provider is unavailable")

    def sync(self, **_: Any) -> Any:
        raise RuntimeError("context provider is unavailable")


class _UnavailableDecisionSourceProvider:
    """Report a configured-but-invalid binding without exposing its config."""

    def __init__(
        self,
        provider_id: str,
        *,
        reason_code: str = "provider_configuration_failed",
    ) -> None:
        self.provider_id = provider_id
        self.reason_code = reason_code

    def scan(
        self,
        *,
        source: DecisionSourceSpec,
        after_cursor: str | None,
        before: str,
        limit: int,
        timeout_seconds: float,
        observed_at: str,
    ) -> DecisionSourceScan:
        del after_cursor, before, limit, timeout_seconds
        return DecisionSourceScan(
            provider_id=self.provider_id,
            source_id=source.source_id,
            status="unavailable",
            observed_at=observed_at,
            requested_limit=source.max_changes,
            reason_code=self.reason_code,
        )

    def exact_read(self, **_: Any) -> Any:
        raise RuntimeError("decision source provider is unavailable")


def _build_source_providers(
    profile: DecisionContextProfile,
    *,
    sources: Sequence[DecisionSourceSpec],
    source_provider_overrides: Mapping[str, DecisionSourceProvider],
) -> dict[str, DecisionSourceProvider]:
    enabled_provider_ids = {source.provider_id for source in sources}
    providers: dict[str, DecisionSourceProvider] = {}
    for provider_id, binding in profile.provider_binding_map().items():
        if provider_id not in enabled_provider_ids:
            continue
        runtime_provider = source_provider_overrides.get(provider_id)
        if runtime_provider is not None:
            if str(getattr(runtime_provider, "provider_id", "") or "") != provider_id:
                providers[provider_id] = _UnavailableDecisionSourceProvider(
                    provider_id,
                    reason_code="runtime_provider_identity_mismatch",
                )
            else:
                providers[provider_id] = runtime_provider
            continue
        try:
            providers[provider_id] = build_decision_source_provider(binding)
        except Exception:
            providers[provider_id] = _UnavailableDecisionSourceProvider(provider_id)
    return providers


def _selected_sources(
    profile: DecisionContextProfile,
    *,
    source_ids: Collection[str] | None,
) -> tuple[DecisionSourceSpec, ...]:
    enabled = {source.source_id: source for source in profile.sources if source.enabled}
    if source_ids is None:
        return tuple(
            source
            for source in profile.sources
            if source.enabled and source.scan_mode != "on_demand"
        )
    if isinstance(source_ids, (str, bytes)):
        raise TypeError("source_ids must be a collection of source ids")
    selected_ids = {str(source_id).strip() for source_id in source_ids}
    unavailable = sorted(
        source_id for source_id in selected_ids if source_id not in enabled
    )
    if unavailable:
        raise ValueError(
            "requested decision sources are unknown or disabled: "
            + ", ".join(unavailable)
        )
    return tuple(enabled[source_id] for source_id in sorted(selected_ids))


def _build_advisory_context_provider(
    profile: DecisionContextProfile,
    *,
    runtime_root: str | Path | None = None,
) -> ContextProvider | None:
    if profile.context_provider is None:
        return None
    private_config = profile.context_provider.get("config", {})
    binding = {
        "provider": profile.context_provider["provider"],
        **(dict(private_config) if isinstance(private_config, Mapping) else {}),
    }
    try:
        if binding["provider"] == EXTENSION_CONTEXT_PROVIDER_ID:
            return build_extension_context_provider(
                binding,
                runtime_root=runtime_root,
            )
        return build_context_provider(binding)
    except Exception:
        return _UnavailableContextProvider()


def _private_profile_digest(profile_path: Path | None) -> str | None:
    if profile_path is None:
        return None
    try:
        return private_file_digest(profile_path)
    except ValueError:
        return None


def _normalized_source_provider_overrides(
    value: Mapping[str, DecisionSourceProvider] | None,
) -> Mapping[str, DecisionSourceProvider]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("source_provider_overrides must be a mapping")
    return value


def assemble_profile_decision_evidence(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path | None,
    decision_id: str,
    observed_at: str,
    before: str,
    rebase: DecisionEvidenceRebaser,
    cursor_path: Path | None = None,
    source_ids: Collection[str] | None = None,
    recall_query: str = "current decision evidence",
    timeout_seconds: float | None = None,
    runtime_root: str | Path | None = None,
    source_provider_overrides: Mapping[str, DecisionSourceProvider] | None = None,
) -> tuple[dict[str, Any], DecisionContextAssembly | None]:
    """Resolve one enabled profile and assemble evidence without applying cursors.

    The caller owns domain reasoning through ``rebase``. Raw exact reads and
    advisory recall stay in-process, while the returned public packet contains
    only opaque refs and compact evidence. ``proposed_cursors`` remain private;
    a caller may keep them in the dedicated pending-settlement store, but must
    not apply them to active cursor state before validated lifecycle writeback.
    """

    profile_digest_before = _private_profile_digest(profile_path)
    provider_overrides = _normalized_source_provider_overrides(
        source_provider_overrides
    )
    activation, profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
        available_source_provider_ids=provider_overrides,
    )
    if activation["available"] is not True or profile is None:
        return activation, None

    context_config = profile.context_provider or {}
    if profile.context_provider is not None and not context_config.get("scope_ref"):
        raise ValueError(
            "context provider scope_ref is required for evidence assembly; "
            "use recall-context for a one-off scope"
        )
    configured_timeout = context_config.get("timeout_seconds", 10.0)
    effective_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(configured_timeout)
    )
    cursors = load_private_decision_cursors(cursor_path, profile=profile)
    sources = _selected_sources(profile, source_ids=source_ids)
    assembly = assemble_decision_evidence(
        goal_id=goal_id,
        decision_id=decision_id,
        observed_at=observed_at,
        before=before,
        sources=sources,
        source_providers=_build_source_providers(
            profile,
            sources=sources,
            source_provider_overrides=provider_overrides,
        ),
        cursors=cursors,
        rebase=rebase,
        context_provider=_build_advisory_context_provider(
            profile,
            runtime_root=runtime_root,
        ),
        context_namespace=str(context_config.get("namespace") or "decision-context"),
        context_scope_ref=str(context_config.get("scope_ref") or "goal"),
        recall_query=recall_query,
        recall_query_summary="current decision evidence",
        recall_limit=int(context_config.get("max_results", 5)),
        timeout_seconds=effective_timeout,
        coverage_sources=profile.sources,
    )
    if profile_path is None or profile_digest_before is None:
        raise ValueError("decision-context profile became unavailable")
    profile_digest_after = private_file_digest(profile_path)
    if profile_digest_before != profile_digest_after:
        raise ValueError("decision-context profile changed during evidence assembly")
    return activation, replace(
        assembly,
        profile_digest=profile_digest_after,
        runtime_bound_provider_ids=tuple(sorted(provider_overrides)),
    )


def _ephemeral_recall_base(activation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": DECISION_CONTEXT_EPHEMERAL_RECALL_SCHEMA_VERSION,
        "capability_id": "decision_context",
        "operation": "ephemeral_recall",
        "goal_id": activation["goal_id"],
        "agent_id": activation["agent_id"],
        "visibility": "local_private_transient",
        "activation": activation,
        "authority": "advisory_only",
        "content_trust": "untrusted_advisory",
        "content_may_instruct": False,
        "source_scan_performed": False,
        "cursor_state_read": False,
        "cursor_state_mutated": False,
        "pending_settlement_written": False,
        "validated_writeback_required": False,
        "profile_write_performed": False,
        "private_locator_persisted": False,
        "external_writes_performed": False,
        "raw_provider_payload_captured": False,
        "raw_content_returned": False,
        "raw_content_persisted": False,
        "execution_authorized": False,
        "durable_promotion_required": True,
        "provider_readiness": None,
        "retrieval_receipt": None,
        "results": [],
    }


def _ephemeral_recall_failure(
    base: Mapping[str, Any],
    *,
    status: str,
    reason_code: object,
) -> dict[str, Any]:
    return dict(base) | {
        "ok": False,
        "status": status,
        "reason_code": reason_code,
    }


def _transient_recall_request(
    context_config: Mapping[str, Any],
    *,
    context_scope_ref: str,
    query: str,
    query_summary: str,
    max_results: int | None,
    timeout_seconds: float | None,
) -> _TransientRecallRequest:
    bounded_scope_ref = str(context_scope_ref or "").strip()
    if not bounded_scope_ref or len(bounded_scope_ref) > 2_048:
        raise ValueError("context_scope_ref must be a bounded non-empty string")
    bounded_query = str(query or "").strip()
    if not bounded_query or len(bounded_query) > 1_000:
        raise ValueError("query must be a bounded non-empty string")
    safe_query_summary = public_safe_compact_text(query_summary, limit=220)
    if safe_query_summary is None:
        raise ValueError("query_summary must be public safe")
    requested_limit = (
        max_results
        if max_results is not None
        else int(context_config.get("max_results", 5))
    )
    if isinstance(requested_limit, bool) or not isinstance(requested_limit, int):
        raise TypeError("max_results must be an integer")
    if not 1 <= requested_limit <= 8:
        raise ValueError("max_results must be between 1 and 8")
    requested_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(context_config.get("timeout_seconds", 10.0))
    )
    if not 1 <= requested_timeout <= 60:
        raise ValueError("timeout_seconds must be between 1 and 60")
    return _TransientRecallRequest(
        scope_ref=bounded_scope_ref,
        query=bounded_query,
        query_summary=safe_query_summary,
        max_results=requested_limit,
        timeout_seconds=requested_timeout,
    )


def recall_profile_decision_context(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path | None,
    context_scope_ref: str,
    query: str,
    query_summary: str,
    observed_at: str,
    max_results: int | None = None,
    timeout_seconds: float | None = None,
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    """Recall one transient scope without scanning or mutating decision state.

    The profile still owns Goal and Agent activation plus provider selection. The
    caller-supplied scope is used only for this provider call and is intentionally
    omitted from the returned packet. Raw result content is returned as explicit
    local-private transient data; the nested retrieval receipt stays public-safe.
    """

    profile_digest_before = _private_profile_digest(profile_path)
    activation, profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
    )
    base = _ephemeral_recall_base(activation)
    if activation.get("available") is not True or profile is None:
        return _ephemeral_recall_failure(
            base,
            status=str(activation.get("status") or "unavailable"),
            reason_code=activation.get("reason_code"),
        )
    if profile_path is None or profile_digest_before is None:
        return _ephemeral_recall_failure(
            base,
            status="profile_invalid",
            reason_code="profile_unavailable_or_invalid",
        )

    context_config = profile.context_provider
    if context_config is None:
        return _ephemeral_recall_failure(
            base,
            status="unavailable",
            reason_code="context_provider_not_configured",
        )
    request = _transient_recall_request(
        context_config,
        context_scope_ref=context_scope_ref,
        query=query,
        query_summary=query_summary,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
    )
    provider = _build_advisory_context_provider(
        profile,
        runtime_root=runtime_root,
    )
    retrieval = collect_context_recall(
        provider=provider,
        namespace=str(context_config.get("namespace") or "decision-context"),
        scope_ref=request.scope_ref,
        query=request.query,
        query_summary=request.query_summary,
        max_results=request.max_results,
        timeout_seconds=request.timeout_seconds,
        observed_at=observed_at,
    )
    if retrieval is None:
        return _ephemeral_recall_failure(
            base,
            status="unavailable",
            reason_code="context_provider_not_configured",
        )
    profile_digest_after = _private_profile_digest(profile_path)
    if profile_digest_before != profile_digest_after:
        return _ephemeral_recall_failure(
            base,
            status="unavailable",
            reason_code="profile_changed_during_recall",
        )

    receipt = retrieval.public_packet()
    results = retrieval.transient_results(
        content_trust="untrusted_advisory",
        content_may_instruct=False,
    )
    return base | {
        "ok": retrieval.status == "completed",
        "status": retrieval.status,
        "reason_code": retrieval.reason_code,
        "provider": retrieval.provider,
        "provider_version": retrieval.provider_version,
        "query_summary": retrieval.query_summary,
        "observed_at": retrieval.observed_at,
        "requested_limit": retrieval.requested_limit,
        "result_count": len(results),
        "provider_readiness": retrieval.provider_readiness,
        "retrieval_receipt": receipt,
        "results": results,
        "raw_content_returned": bool(results),
    }
