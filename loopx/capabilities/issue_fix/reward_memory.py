from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import time
import hashlib
import json
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...domain_packs.issue_fix import REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN
from ..context_providers.base import ContextProvider
from ..reward_memory.application import (
    RewardMemoryApplier,
    RewardMemoryRecallItem,
)
from ..reward_memory.candidate_review import build_issue_fix_reward_memory_candidate
from ..reward_memory.ingestion import (
    ingest_reward_memory_candidate,
    normalize_reward_memory_standing_policy,
)
from ..reward_memory.experiment import resolve_reward_memory_surface_config
from ..reward_memory.runtime_hooks import (
    run_reward_memory_automatic_recall_hook,
)
from ..reward_memory.scoped_feedback import _GUARD_FIELDS, _REASONING_FIELDS, _SOURCE_FIELDS
from ..semantic_preference.reward_memory import run_semantic_preference_reward_memory


Payload = dict[str, object]
PayloadView = Mapping[str, object]


@runtime_checkable
class _RecallItemView(Protocol):
    target_class: str
    content_summary: str


def _mapping(value: object) -> PayloadView:
    return value if isinstance(value, Mapping) else {}


ISSUE_FIX_PATCH_PLANNING_SURFACE = "issue_fix.patch_planning"
ISSUE_FIX_REVIEWER_ARTIFACT_SURFACE = "reviewer_artifact.summary"
ISSUE_FIX_REVIEWER_NOTIFICATION_SURFACE = "reviewer_notification.before_send"
ISSUE_FIX_REWARD_MEMORY_APPLICATION_SCHEMA_VERSION = (
    "issue_fix_reward_memory_application_v0"
)
ISSUE_FIX_REVIEWER_ARTIFACT_SCHEMA_VERSION = "issue_fix_reviewer_artifact_v0"
ISSUE_FIX_REVIEWER_ARTIFACT_APPLICATION_SCHEMA_VERSION = (
    "issue_fix_reviewer_artifact_reward_memory_application_v0"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_APPLICATION_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_reward_memory_application_v0"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_DECISION_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_before_send_v0"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_POLICY_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_delivery_policy_v0"
)
ISSUE_FIX_REWARD_MEMORY_EVENT_SCHEMA_VERSION = "issue_fix_reward_memory_event_v0"
ISSUE_FIX_REWARD_MEMORY_EXECUTION_EVIDENCE_SCHEMA_VERSION = (
    "issue_fix_reward_memory_execution_evidence_v0"
)

_EVENT_FIELDS = {
    "schema_version",
    "issue_ref",
    "workspace_ref",
    "repository_ref",
    "surface_id",
    "revision_ref",
    "target_class",
    "content_summary",
    "source",
    "reasoning",
    "guard_context",
    "requested_action_scopes",
    "raw_content_captured",
}


def _execution_evidence(
    recall: PayloadView | None,
    application: PayloadView | None = None,
    *,
    query_kind: str | None = None,
) -> Payload:
    """Build the minimum evidence needed for conservative outcome reporting."""

    recall_packet = _mapping(recall)
    application_packet = _mapping(application)
    receipt_raw = application_packet.get("receipt")
    receipt = receipt_raw if isinstance(receipt_raw, Mapping) else None
    raw_calls = recall_packet.get("provider_call_count")
    provider_call_count = int(raw_calls) if isinstance(raw_calls, (int, float, str)) and raw_calls else 0
    effective_query_kind = str(
        query_kind or recall_packet.get("query_kind") or "business_recall"
    )
    exact_readback_verified = recall_packet.get("result_readback_verified") is True
    query_evidence_raw = recall_packet.get("query_evidence")
    query_evidence = (
        [dict(item) for item in query_evidence_raw if isinstance(item, Mapping)]
        if isinstance(query_evidence_raw, (list, tuple))
        else []
    )
    query_evidence_verified = bool(query_evidence) and all(
        len(str(item.get("query_digest") or "")) == 16
        and bool(str(item.get("query_summary") or "").strip())
        and item.get("exact_query_exposed") is False
        for item in query_evidence
    )
    provider_recall_verified = bool(
        provider_call_count > 0 and exact_readback_verified and query_evidence_verified
    )
    receipt_verified = bool(
        receipt
        and effective_query_kind == "business_recall"
        and receipt.get("schema_version") == "reward_memory_application_receipt_v0"
        and receipt.get("query_kind") == "business_recall"
        and receipt.get("query_evidence") == query_evidence
        and receipt.get("provider_call_count") == provider_call_count
        and provider_recall_verified
        and receipt.get("outcome") == "applied"
        and receipt.get("result_readback_verified") is True
        and receipt.get("current_artifact_verified") is True
        and receipt.get("memory_ref_digests")
    )
    if effective_query_kind == "ingest_verification" and provider_recall_verified:
        verified_result = "ingest_exact_readback_verified"
    elif effective_query_kind == "ingest_verification" and provider_call_count:
        verified_result = "ingest_provider_called_without_verified_readback"
    elif effective_query_kind == "ingest_verification":
        verified_result = "ingest_provider_not_called"
    elif receipt_verified:
        verified_result = "memory_applied"
    elif provider_recall_verified:
        verified_result = "memory_recalled_not_applied"
    elif provider_call_count:
        verified_result = "provider_called_without_verified_recall"
    else:
        verified_result = "provider_not_called"

    unknowns: list[str] = []
    if provider_call_count == 0:
        unknowns.append("provider_result_unknown")
    if not query_evidence_verified:
        unknowns.append("query_evidence_unknown")
    if not exact_readback_verified:
        unknowns.append("exact_recall_readback_unknown")
    if (
        effective_query_kind == "business_recall"
        and application is not None
        and not receipt_verified
    ):
        unknowns.append("memory_application_effect_unknown")

    return {
        "schema_version": ISSUE_FIX_REWARD_MEMORY_EXECUTION_EVIDENCE_SCHEMA_VERSION,
        "verified_result": verified_result,
        "unknowns": unknowns,
        "minimum_evidence": {
            "query_kind": effective_query_kind,
            "query_evidence": query_evidence,
            "provider_call_count": provider_call_count,
            "exact_readback_verified": exact_readback_verified,
            "application_receipt": dict(receipt) if receipt is not None else None,
        },
        "claim_policy": (
            "claim_recalled_only_after_query_provider_call_and_exact_readback_and_"
            "claim_applied_only_after_matching_verified_application_receipt"
        ),
        "raw_content_captured": False,
    }


def _strict_object(
    value: object,
    *,
    label: str,
    allowed: set[str],
) -> PayloadView:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(
            f"{label} contains unsupported fields: {', '.join(unexpected)}"
        )
    return value


def _append_receipt_reasons(packet: PayloadView, reasons: list[str]) -> PayloadView:
    shared = _mapping(packet.get("application"))
    receipt = _mapping(shared.get("receipt"))
    if shared.get("status") != "applied":
        reasons.append("memory_not_applied")
    if receipt.get("schema_version") != "reward_memory_application_receipt_v0":
        reasons.append("application_receipt_invalid")
    if receipt.get("current_artifact_verified") is not True:
        reasons.append("current_artifact_unverified")
    if receipt.get("result_readback_verified") is not True:
        reasons.append("memory_readback_unverified")
    digests = receipt.get("memory_ref_digests")
    if not isinstance(digests, list) or not digests:
        reasons.append("memory_attribution_missing")
    return receipt


_REVIEWER_ARTIFACT_QUERIES: tuple[Payload, ...] = (
    {
        "query": "Which reviewed policy governs this reviewer-facing PR summary?",
        "query_summary": "reviewer-facing PR summary policy",
    },
)


def _run_automatic_surface_recall(
    *,
    surface_id: str,
    schema_version: str,
    output_key: str,
    adapter_role: str,
    fail_open_message: str,
    include_peer_ref: bool = False,
    experiment_config: PayloadView,
    base_output: PayloadView,
    revision_ref: str,
    queries: Sequence[PayloadView],
    observed_at: str,
    freshness_context: PayloadView,
    conflict_state: str,
    application_id: str,
    artifact_ref: str | None,
    apply_memory: RewardMemoryApplier | None,
    provider: ContextProvider | None,
    output_builder: Callable[[PayloadView], Payload],
) -> Payload:
    route = resolve_reward_memory_surface_config(experiment_config, surface_id)
    scope = _mapping(_mapping(route.get("corpus")).get("scope"))
    workspace_ref = str(scope["workspace_ref"])
    repository_ref = str(scope["project_ref"])
    recall_corpora = route.get("recall_corpora")
    corpora = recall_corpora if isinstance(recall_corpora, Sequence) else ()
    checkpoints: dict[str, Payload] = {
        str(corpus_entry["corpus_id"]): {
            "verified": policy_entry["enabled"] is True,
            "corpus_id": str(corpus_entry["corpus_id"]),
            "workspace_ref": workspace_ref,
            "project_ref": repository_ref,
            "surface_id": surface_id,
            "read_authority": corpus_entry["read_authority"],
            "source_ref": policy_entry["authority_source_ref"],
            **(
                {"peer_ref": _mapping(corpus_entry.get("scope")).get("peer_ref")}
                if include_peer_ref
                else {}
            ),
        }
        for entry in corpora
        for corpus_entry in (_mapping(_mapping(entry).get("corpus")),)
        for policy_entry in (_mapping(_mapping(entry).get("standing_policy")),)
    }
    automatic = run_reward_memory_automatic_recall_hook(
        experiment_config,
        surface_id=surface_id,
        base_output=base_output,
        workspace_ref=workspace_ref,
        project_ref=repository_ref,
        revision_ref=revision_ref,
        peer_ref=(str(scope.get("peer_ref") or "") or None) if include_peer_ref else None,
        queries=queries,
        observed_at=observed_at,
        freshness_context=dict(freshness_context),
        conflict_state=conflict_state,
        read_authority_checkpoints=checkpoints,
        application_id=application_id,
        artifact_ref=artifact_ref,
        apply_memory=apply_memory,
        provider=provider,
    )
    output = automatic.get("output")
    if not isinstance(output, Mapping):
        raise AssertionError(fail_open_message)
    attempts = automatic.get("recall_attempts")
    recall_attempts = attempts if isinstance(attempts, list) else []
    shared_app = automatic.get("application")
    application = (
        dict(shared_app)
        if isinstance(shared_app, Mapping)
        else {"status": automatic.get("status")}
    )
    return {
        "ok": True,
        "schema_version": schema_version,
        "surface_id": surface_id,
        output_key: output_builder(output),
        "recall": recall_attempts[-1] if recall_attempts else {"status": automatic["status"]},
        "recall_attempts": recall_attempts,
        "application": application,
        "telemetry": automatic["telemetry"],
        "shared_core": "loopx.capabilities.reward_memory.runtime_hooks",
        "adapter_role": adapter_role,
        "automatic_recall": automatic["automatic_recall"],
        "provider_failure_is_user_gate": False,
        "external_writes_performed": False,
    }


def ingest_issue_fix_reward_memory_event(
    event: PayloadView,
    *,
    corpus: PayloadView,
    standing_policy: PayloadView,
    provider_binding: PayloadView,
    observed_at: str,
    execute: bool = False,
    provider: ContextProvider | None = None,
) -> Payload:
    """Map one compact Issue Fix feedback event into the shared ingest seam."""

    raw = _strict_object(event, label="issue_fix_event", allowed=_EVENT_FIELDS)
    if raw.get("schema_version") != ISSUE_FIX_REWARD_MEMORY_EVENT_SCHEMA_VERSION:
        raise ValueError("event must use issue_fix_reward_memory_event_v0")
    for field, allowed in (
        ("source", _SOURCE_FIELDS),
        ("reasoning", _REASONING_FIELDS),
        ("guard_context", _GUARD_FIELDS),
    ):
        _strict_object(raw.get(field), label=f"issue_fix_event.{field}", allowed=allowed)
    policy = normalize_reward_memory_standing_policy(standing_policy)
    source = raw["source"]
    assert isinstance(source, Mapping)
    authority_checkpoint = None
    if raw.get("target_class") == "hard_policy":
        authority_checkpoint = {
            "verified": policy["enabled"] is True,
            "source_ref": policy["authority_source_ref"],
            "actor_ref": source.get("actor_ref"),
            "actor_role": source.get("actor_role"),
            "project_ref": raw.get("repository_ref"),
            "action_scopes": raw.get("requested_action_scopes") or [],
        }
    adapter = build_issue_fix_reward_memory_candidate(
        raw,
        authority_checkpoint=authority_checkpoint,
    )
    result = ingest_reward_memory_candidate(
        adapter["shared_candidate"],
        corpus=corpus,
        standing_policy=policy,
        provider_binding=provider_binding,
        observed_at=observed_at,
        execute=execute,
        provider=provider,
    )
    summary = public_safe_compact_text(raw.get("content_summary"), limit=500) or ""
    readback_raw = result.get("readback")
    readback = readback_raw if isinstance(readback_raw, Mapping) else None
    return result | {
        "adapter_schema_version": ISSUE_FIX_REWARD_MEMORY_EVENT_SCHEMA_VERSION,
        "issue_ref": public_safe_compact_text(raw.get("issue_ref"), limit=160),
        "event_summary_digest": hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16],
        "execution_evidence": _execution_evidence(
            readback, query_kind="ingest_verification"
        ),
        "next_issue_fix_call": "run_issue_fix_patch_planning_reward_memory",
    }


def run_issue_fix_patch_planning_reward_memory(
    base_plan: PayloadView,
    *,
    corpus: PayloadView,
    workspace_ref: str,
    repository_ref: str,
    revision_ref: str,
    queries: Sequence[PayloadView],
    mode: str,
    observed_at: str,
    freshness_context: PayloadView,
    conflict_state: str,
    read_authority_checkpoint: PayloadView,
    provider_binding: PayloadView,
    application_id: str,
    peer_ref: str | None = None,
    artifact_ref: str | None = None,
    apply_memory: RewardMemoryApplier | None = None,
    provider: ContextProvider | None = None,
    limit: int = 5,
) -> Payload:
    """Apply opt-in reward memory at the Issue Fix patch-planning boundary."""

    guarded_apply: RewardMemoryApplier | None = None
    if apply_memory is not None:

        def _apply_patch_plan(
            base: object, items: tuple[RewardMemoryRecallItem, ...]
        ) -> PayloadView:
            decision = apply_memory(base, items)
            output = decision.get("output") if isinstance(decision, Mapping) else None
            if not isinstance(output, Mapping):
                raise ValueError("Issue Fix reward-memory output must be a patch plan")
            return decision

        guarded_apply = _apply_patch_plan

    request: Payload = {
        "workspace_ref": workspace_ref,
        "project_ref": repository_ref,
        "surface_id": ISSUE_FIX_PATCH_PLANNING_SURFACE,
        "revision_ref": revision_ref,
        "mode": mode,
        "query_kind": "business_recall",
        "queries": list(queries),
        "limit": limit,
        "observed_at": observed_at,
        "freshness_context": dict(freshness_context),
        "conflict_state": conflict_state,
        "raw_content_captured": False,
    }
    if peer_ref is not None:
        request["peer_ref"] = peer_ref
    shared = run_semantic_preference_reward_memory(
        dict(base_plan),
        corpus=corpus,
        request=request,
        read_authority_checkpoint=read_authority_checkpoint,
        provider_binding=provider_binding,
        application_id=application_id,
        artifact_ref=artifact_ref,
        apply_memory=guarded_apply,
        provider=provider,
    )
    output = shared["output"]
    if not isinstance(output, Mapping):
        raise AssertionError("Issue Fix fail-open invariant returned a non-plan output")
    return {
        "ok": True,
        "schema_version": ISSUE_FIX_REWARD_MEMORY_APPLICATION_SCHEMA_VERSION,
        "surface_id": ISSUE_FIX_PATCH_PLANNING_SURFACE,
        "patch_plan": dict(output),
        "recall": shared["recall"],
        "application": shared["application"],
        "execution_evidence": _execution_evidence(
            shared["recall"], shared["application"]
        ),
        "shared_core": shared["shared_core"],
        "adapter_role": "field_mapping_only_model_owns_patch_tradeoffs",
        "automatic_recall": False,
        "provider_failure_is_user_gate": False,
    }


def _reviewer_artifact(value: PayloadView) -> Payload:
    artifact: Payload = {
        "schema_version": ISSUE_FIX_REVIEWER_ARTIFACT_SCHEMA_VERSION,
        "repo": public_safe_compact_text(value.get("repo"), limit=200),
        "pr_ref": public_safe_compact_text(value.get("pr_ref"), limit=40),
        "permalink": public_safe_compact_text(value.get("permalink"), limit=300),
        "source_title": public_safe_compact_text(value.get("source_title"), limit=180),
        "summary": public_safe_compact_text(value.get("summary"), limit=220),
    }
    if not all(artifact[key] for key in ("repo", "pr_ref", "permalink")):
        raise ValueError("reviewer artifact must identify the current PR")
    return artifact


def _reviewer_artifact_applier(
    base: PayloadView,
    *,
    reviewer_summary: str | None,
    reasoning_summary: str | None,
) -> RewardMemoryApplier | None:
    proposed_summary = public_safe_compact_text(reviewer_summary, limit=220)
    proposed_reasoning = public_safe_compact_text(reasoning_summary, limit=500)
    if not proposed_summary or not proposed_reasoning:
        return None

    def apply_memory(
        current: object, items: tuple[RewardMemoryRecallItem, ...]
    ) -> PayloadView:
        if not isinstance(current, Mapping):
            raise ValueError("reviewer artifact base must be an object")
        verified = _reviewer_artifact(current)
        output = {**verified, "summary": proposed_summary}
        return {
            "outcome": "applied",
            "output": output,
            "memory_refs": [item.memory_ref for item in items],
            "reasoning_summary": proposed_reasoning,
            "current_artifact_verified": all(
                output[key] == base[key]
                for key in ("repo", "pr_ref", "permalink", "source_title")
            ),
        }

    return apply_memory


def reviewer_artifact_notification_gate(application: PayloadView | None) -> Payload:
    """Validate the compact receipt required before reviewer-facing delivery."""

    reasons: list[str] = []
    packet = _mapping(application)
    artifact = _mapping(packet.get("reviewer_artifact"))
    summary = public_safe_compact_text(artifact.get("summary"), limit=220)
    if packet.get("schema_version") != ISSUE_FIX_REVIEWER_ARTIFACT_APPLICATION_SCHEMA_VERSION:
        reasons.append("application_schema_invalid")
    if packet.get("surface_id") != ISSUE_FIX_REVIEWER_ARTIFACT_SURFACE:
        reasons.append("surface_mismatch")
    if artifact.get("schema_version") != ISSUE_FIX_REVIEWER_ARTIFACT_SCHEMA_VERSION:
        reasons.append("artifact_schema_invalid")
    if not summary or not any("\u4e00" <= char <= "\u9fff" for char in summary):
        reasons.append("concise_chinese_summary_missing")
    receipt = _append_receipt_reasons(packet, reasons)
    return {
        "schema_version": "issue_fix_reviewer_artifact_notification_gate_v0",
        "passed": not reasons,
        "status": "ready" if not reasons else "blocked",
        "reason_codes": reasons,
        "surface_id": ISSUE_FIX_REVIEWER_ARTIFACT_SURFACE,
        "summary": summary or None,
        "application_receipt": dict(receipt) if receipt else None,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
    }


def run_issue_fix_reviewer_artifact_reward_memory(
    base_artifact: PayloadView,
    *,
    reviewer_summary: str | None,
    reasoning_summary: str | None,
    corpus: PayloadView,
    workspace_ref: str,
    repository_ref: str,
    revision_ref: str,
    observed_at: str,
    freshness_context: PayloadView,
    conflict_state: str,
    read_authority_checkpoint: PayloadView,
    provider_binding: PayloadView,
    application_id: str,
    peer_ref: str | None = None,
    artifact_ref: str | None = None,
    provider: ContextProvider | None = None,
    limit: int = 3,
) -> Payload:
    """Apply exact reviewed policy to one current reviewer-facing PR summary.

    The caller/model owns the proposed summary and its reasoning. This adapter
    only preserves PR identity, attributes recalled memory, and emits the
    compact receipt consumed by the external notification gate.
    """

    base = _reviewer_artifact(base_artifact)
    request: Payload = {
        "workspace_ref": workspace_ref,
        "project_ref": repository_ref,
        "surface_id": ISSUE_FIX_REVIEWER_ARTIFACT_SURFACE,
        "revision_ref": revision_ref,
        "mode": "function_boundary",
        "queries": list(_REVIEWER_ARTIFACT_QUERIES),
        "limit": limit,
        "observed_at": observed_at,
        "freshness_context": dict(freshness_context),
        "conflict_state": conflict_state,
        "raw_content_captured": False,
    }
    if peer_ref is not None:
        request["peer_ref"] = peer_ref
    shared = run_semantic_preference_reward_memory(
        base,
        corpus=corpus,
        request=request,
        read_authority_checkpoint=read_authority_checkpoint,
        provider_binding=provider_binding,
        application_id=application_id,
        artifact_ref=artifact_ref,
        apply_memory=_reviewer_artifact_applier(
            base,
            reviewer_summary=reviewer_summary,
            reasoning_summary=reasoning_summary,
        ),
        provider=provider,
    )
    output = shared.get("output")
    if not isinstance(output, Mapping):
        raise AssertionError("reviewer artifact fail-open invariant was violated")
    result: Payload = {
        "ok": True,
        "schema_version": ISSUE_FIX_REVIEWER_ARTIFACT_APPLICATION_SCHEMA_VERSION,
        "surface_id": ISSUE_FIX_REVIEWER_ARTIFACT_SURFACE,
        "reviewer_artifact": _reviewer_artifact(output),
        "recall": shared["recall"],
        "application": shared["application"],
        "shared_core": shared["shared_core"],
        "adapter_role": "identity_guard_only_model_owns_summary_reasoning",
        "automatic_recall": False,
        "provider_failure_is_user_gate": False,
    }
    result["notification_gate"] = reviewer_artifact_notification_gate(result)
    return result


def run_issue_fix_reviewer_artifact_automatic_reward_memory(
    base_artifact: PayloadView,
    *,
    reviewer_summary: str | None,
    reasoning_summary: str | None,
    experiment_config: PayloadView,
    revision_ref: str,
    observed_at: str,
    freshness_context: PayloadView,
    conflict_state: str,
    application_id: str,
    artifact_ref: str | None = None,
    provider: ContextProvider | None = None,
) -> Payload:
    """Run the opt-in automatic hook at the reviewer-artifact boundary."""

    base = _reviewer_artifact(base_artifact)
    result = _run_automatic_surface_recall(
        surface_id=ISSUE_FIX_REVIEWER_ARTIFACT_SURFACE,
        schema_version=ISSUE_FIX_REVIEWER_ARTIFACT_APPLICATION_SCHEMA_VERSION,
        output_key="reviewer_artifact",
        adapter_role="identity_guard_only_model_owns_summary_reasoning",
        fail_open_message="automatic reviewer artifact fail-open was violated",
        include_peer_ref=True,
        experiment_config=experiment_config,
        base_output=base,
        revision_ref=revision_ref,
        queries=_REVIEWER_ARTIFACT_QUERIES,
        observed_at=observed_at,
        freshness_context=freshness_context,
        conflict_state=conflict_state,
        application_id=application_id,
        artifact_ref=artifact_ref,
        apply_memory=_reviewer_artifact_applier(
            base, reviewer_summary=reviewer_summary, reasoning_summary=reasoning_summary
        ),
        provider=provider,
        output_builder=_reviewer_artifact,
    )
    result["notification_gate"] = reviewer_artifact_notification_gate(result)
    return result


def _reviewer_notification_delivery_policy(value: object) -> Payload:
    if not isinstance(value, Mapping):
        raise ValueError("reviewer notification delivery policy must be an object")
    allowed_local_time = value.get("allowed_local_time")
    if not isinstance(allowed_local_time, Mapping):
        raise ValueError("allowed_local_time must be an object")
    timezone_name = str(value.get("timezone") or "").strip()
    start = str(allowed_local_time.get("start") or "").strip()
    end = str(allowed_local_time.get("end") or "").strip()
    if (
        not timezone_name
        or not REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.fullmatch(start)
        or not REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.fullmatch(end)
        or start == end
        or value.get("outside_window") != "queue_without_send"
    ):
        raise ValueError("reviewer notification delivery policy is invalid")
    try:
        ZoneInfo(timezone_name)
        time.fromisoformat(start)
        time.fromisoformat(end)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("reviewer notification delivery policy is invalid") from exc
    return {
        "timezone": timezone_name,
        "allowed_local_time": {"start": start, "end": end},
        "outside_window": "queue_without_send",
    }


def _reviewer_notification_before_send_base(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    delivery_policy: PayloadView | None,
) -> Payload:
    policy = (
        _reviewer_notification_delivery_policy(delivery_policy)
        if delivery_policy is not None
        else None
    )
    return {
        "schema_version": ISSUE_FIX_REVIEWER_NOTIFICATION_DECISION_SCHEMA_VERSION,
        "repo": public_safe_compact_text(repo, limit=200),
        "pr_ref": f"#{int(pr_number)}",
        "permalink": public_safe_compact_text(pr_url, limit=300),
        "delivery_policy": policy,
        "policy_source": "sink_config" if policy else "default_unrestricted",
    }


def _reviewer_notification_policy_from_item(item: object) -> Payload | None:
    if not isinstance(item, _RecallItemView) or item.target_class != "hard_policy":
        return None
    try:
        payload = json.loads(str(item.content_summary or ""))
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version")
        != ISSUE_FIX_REVIEWER_NOTIFICATION_POLICY_SCHEMA_VERSION
    ):
        return None
    try:
        return _reviewer_notification_delivery_policy(payload.get("delivery_policy"))
    except ValueError:
        return None


def _reviewer_notification_before_send_applier(
    base: PayloadView,
) -> RewardMemoryApplier:
    def apply_memory(
        current: object, items: tuple[RewardMemoryRecallItem, ...]
    ) -> PayloadView:
        if not isinstance(current, Mapping) or any(
            current.get(key) != base.get(key)
            for key in ("schema_version", "repo", "pr_ref", "permalink")
        ):
            raise ValueError("reviewer notification artifact changed before apply")
        policies: dict[str, tuple[Payload, list[str]]] = {}
        for item in items:
            policy = _reviewer_notification_policy_from_item(item)
            if policy is None:
                continue
            canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
            refs = policies.setdefault(canonical, (policy, []))[1]
            refs.append(str(item.memory_ref))
        if len(policies) != 1:
            return {
                "outcome": "ignored",
                "output": dict(base),
                "memory_refs": [ref for _, refs in policies.values() for ref in refs],
                "reasoning_summary": (
                    "No single active structured reviewer delivery policy was recalled."
                ),
                "current_artifact_verified": True,
            }
        canonical, (policy, refs) = next(iter(policies.items()))
        return {
            "outcome": "applied",
            "output": {
                **dict(base),
                "delivery_policy": policy,
                "policy_source": "reward_memory",
                "policy_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
            },
            "memory_refs": refs,
            "reasoning_summary": (
                "Applied the single exact hard-policy record at the pre-send boundary."
            ),
            "current_artifact_verified": True,
        }

    return apply_memory


def reviewer_notification_before_send_gate(
    application: PayloadView | None,
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
) -> Payload:
    packet = _mapping(application)
    decision = _mapping(packet.get("decision"))
    reasons: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_REVIEWER_NOTIFICATION_APPLICATION_SCHEMA_VERSION:
        reasons.append("application_schema_invalid")
    if packet.get("surface_id") != ISSUE_FIX_REVIEWER_NOTIFICATION_SURFACE:
        reasons.append("surface_mismatch")
    if decision.get("schema_version") != ISSUE_FIX_REVIEWER_NOTIFICATION_DECISION_SCHEMA_VERSION:
        reasons.append("decision_schema_invalid")
    if not (
        decision.get("repo") == public_safe_compact_text(repo, limit=200)
        and decision.get("pr_ref") == f"#{int(pr_number)}"
        and decision.get("permalink") == public_safe_compact_text(pr_url, limit=300)
    ):
        reasons.append("current_artifact_identity_mismatch")
    try:
        policy: Payload | None = _reviewer_notification_delivery_policy(
            decision.get("delivery_policy")
        )
    except ValueError:
        policy = None
        reasons.append("delivery_policy_invalid")
    if decision.get("policy_source") != "reward_memory":
        reasons.append("memory_policy_not_applied")
    receipt = _append_receipt_reasons(packet, reasons)
    return {
        "schema_version": "issue_fix_reviewer_notification_before_send_gate_v0",
        "passed": not reasons,
        "status": "applied" if not reasons else "fail_open",
        "reason_codes": reasons,
        "surface_id": ISSUE_FIX_REVIEWER_NOTIFICATION_SURFACE,
        "delivery_policy": policy if not reasons else None,
        "application_receipt": dict(receipt) if receipt else None,
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
    }


def run_issue_fix_reviewer_notification_automatic_reward_memory(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    delivery_policy: PayloadView | None,
    experiment_config: PayloadView,
    revision_ref: str,
    observed_at: str,
    freshness_context: PayloadView,
    conflict_state: str,
    application_id: str,
    provider: ContextProvider | None = None,
) -> Payload:
    """Recall one structured hard policy immediately before Lark delivery."""

    base = _reviewer_notification_before_send_base(
        repo=repo, pr_number=pr_number, pr_url=pr_url, delivery_policy=delivery_policy
    )
    result = _run_automatic_surface_recall(
        surface_id=ISSUE_FIX_REVIEWER_NOTIFICATION_SURFACE,
        schema_version=ISSUE_FIX_REVIEWER_NOTIFICATION_APPLICATION_SCHEMA_VERSION,
        output_key="decision",
        adapter_role="structured_policy_guard_existing_sink_owns_send_or_queue",
        fail_open_message="automatic pre-send reward-memory fail-open was violated",
        experiment_config=experiment_config,
        base_output=base,
        revision_ref=revision_ref,
        queries=(
            {
                "query": (
                    "Which active reviewed hard policy constrains whether this "
                    "reviewer notification may be sent now?"
                ),
                "query_summary": "reviewer notification pre-send policy",
            },
        ),
        observed_at=observed_at,
        freshness_context=freshness_context,
        conflict_state=conflict_state,
        application_id=application_id,
        artifact_ref=f"github:{repo}#pr-{int(pr_number)}",
        apply_memory=_reviewer_notification_before_send_applier(base),
        provider=provider,
        output_builder=dict,
    )
    result["before_send_gate"] = reviewer_notification_before_send_gate(
        result, repo=repo, pr_number=pr_number, pr_url=pr_url
    )
    return result
