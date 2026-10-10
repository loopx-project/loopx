"""Decision-driven, provider-neutral material rerank and exploration planning."""

from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..decision_context.packets import (
    DECISION_EVIDENCE_PACKET_SCHEMA_VERSION,
    build_decision_evidence_packet,
)
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
from .ranking import build_material_rerank_proposal

MATERIAL_EXPLORE_INTENT_SCHEMA_VERSION = "material_explore_intent_v0"
_CURRENT_FRESHNESS_VALUES = frozenset({"current", "fresh"})

_EXPLORE_TOPIC_FIELDS = {
    "evidence_refs",
    "reason_code",
    "topic_ref",
}
_EXPLORE_TOPIC_REQUIRED_FIELDS = {
    "reason_code",
    "topic_ref",
}
_DECISION_EVIDENCE_FIELDS = {
    "capability",
    "changed_facts",
    "conflicts",
    "credentials_captured",
    "decision_id",
    "external_writes_performed",
    "goal_id",
    "observed_at",
    "packet_ref",
    "provider_fail_open",
    "provider_health",
    "raw_context_captured",
    "recalled_claims",
    "schema_version",
    "source_revisions",
    "stale_or_rejected_claims",
    "visibility",
}


@dataclass(frozen=True)
class MaterialDecisionPolicyResult:
    """Transient policy output; only validated refs may enter public packets."""

    moves: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    explore_topics: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    no_change_reason: str | None = None


class MaterialDecisionPolicy(Protocol):
    """Replaceable policy that consumes public Decision Context evidence."""

    policy_id: str

    def evaluate(
        self,
        *,
        goal_id: str,
        decision_evidence: Mapping[str, Any],
        inventory_ref: str,
        observed_at: str,
        target_window_size: int,
        max_moved_items: int,
        max_rank_displacement: int,
        protected_material_refs: tuple[str, ...],
        max_explore_topics: int,
        max_provider_calls: int,
        max_new_candidates: int,
    ) -> MaterialDecisionPolicyResult: ...


@dataclass(frozen=True)
class MaterialDecisionPlanning:
    """Validated planning result; it never applies queue or provider effects."""

    rerank_proposal: Mapping[str, Any]
    explore_intent: Mapping[str, Any] | None
    policy_status: str
    readiness_blockers: tuple[str, ...] = ()

    @property
    def no_change(self) -> bool:
        return bool(self.rerank_proposal["no_change"])


def _normalize_explore_topics(
    values: Sequence[Mapping[str, Any]],
    *,
    max_explore_topics: int,
    max_provider_calls: int,
) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("topics must be a sequence of objects")
    if not values:
        raise ValueError("topics must contain at least one exploration topic")
    if len(values) > max_explore_topics:
        raise ValueError("topics exceeds max_explore_topics")
    if len(values) > max_provider_calls:
        raise ValueError("topics exceeds max_provider_calls")

    normalized: list[dict[str, Any]] = []
    topic_refs: set[str] = set()
    for index, value in enumerate(values):
        field_name = f"topics[{index}]"
        check_record_keys(
            value,
            field=field_name,
            allowed=_EXPLORE_TOPIC_FIELDS,
            required=_EXPLORE_TOPIC_REQUIRED_FIELDS,
        )
        topic_ref = compact_token(
            value["topic_ref"],
            field=f"{field_name}.topic_ref",
        )
        if topic_ref in topic_refs:
            raise ValueError("topics topic_ref values must be unique")
        topic_refs.add(topic_ref)
        normalized.append(
            {
                "topic_ref": topic_ref,
                "reason_code": compact_token(
                    value["reason_code"],
                    field=f"{field_name}.reason_code",
                ),
                "evidence_refs": token_list(
                    value.get("evidence_refs"),
                    field=f"{field_name}.evidence_refs",
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


def build_material_explore_intent(
    *,
    goal_id: str,
    intent_id: str,
    inventory_ref: str,
    decision_evidence_ref: str,
    policy_ref: str,
    observed_at: str,
    max_explore_topics: int,
    max_provider_calls: int,
    max_new_candidates: int,
    stop_condition: str,
    topics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an analysis-only exploration intent with opaque topic refs."""

    max_topics = positive_int(
        max_explore_topics,
        field="max_explore_topics",
    )
    max_calls = positive_int(
        max_provider_calls,
        field="max_provider_calls",
    )
    max_candidates = positive_int(
        max_new_candidates,
        field="max_new_candidates",
    )
    normalized_topics = _normalize_explore_topics(
        topics,
        max_explore_topics=max_topics,
        max_provider_calls=max_calls,
    )
    intent: dict[str, Any] = {
        "schema_version": MATERIAL_EXPLORE_INTENT_SCHEMA_VERSION,
        "goal_id": compact_token(goal_id, field="goal_id"),
        "intent_id": compact_token(intent_id, field="intent_id"),
        "inventory_ref": compact_token(
            inventory_ref,
            field="inventory_ref",
        ),
        "decision_evidence_ref": compact_token(
            decision_evidence_ref,
            field="decision_evidence_ref",
        ),
        "policy_ref": compact_token(policy_ref, field="policy_ref"),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "visibility": "public_safe",
        "capability": capability_contract(packet_role="explore_intent"),
        "constraints": {
            "max_explore_topics": max_topics,
            "max_provider_calls": max_calls,
            "max_new_candidates": max_candidates,
            "stop_condition": compact_text(
                stop_condition,
                field="stop_condition",
            ),
        },
        "topics": normalized_topics,
        "execution_authorized": False,
        "provider_calls_performed": False,
        "source_cursor_apply_authorized": False,
        "raw_queries_captured": False,
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    intent["intent_ref"] = packet_ref("material-explore", intent)
    return intent


def _validated_decision_evidence(
    decision_evidence: Mapping[str, Any],
    *,
    goal_id: str,
) -> dict[str, Any]:
    if not isinstance(decision_evidence, Mapping):
        raise TypeError("decision_evidence must be an object")
    check_record_keys(
        decision_evidence,
        field="decision_evidence",
        allowed=_DECISION_EVIDENCE_FIELDS,
        required=_DECISION_EVIDENCE_FIELDS,
    )
    if (
        decision_evidence.get("schema_version")
        != DECISION_EVIDENCE_PACKET_SCHEMA_VERSION
    ):
        raise ValueError("decision_evidence must use decision_evidence_packet_v0")
    if compact_token(decision_evidence.get("goal_id"), field="goal_id") != goal_id:
        raise ValueError("decision_evidence goal_id does not match")
    if decision_evidence.get("visibility") != "public_safe":
        raise ValueError("decision_evidence must be public_safe")
    if decision_evidence.get("raw_context_captured") is not False:
        raise ValueError("decision_evidence must not capture raw context")
    if decision_evidence.get("credentials_captured") is not False:
        raise ValueError("decision_evidence must not capture credentials")

    rebuilt = build_decision_evidence_packet(
        goal_id=decision_evidence["goal_id"],
        decision_id=decision_evidence["decision_id"],
        observed_at=decision_evidence["observed_at"],
        changed_facts=decision_evidence["changed_facts"],
        recalled_claims=decision_evidence["recalled_claims"],
        stale_or_rejected_claims=decision_evidence["stale_or_rejected_claims"],
        conflicts=decision_evidence["conflicts"],
        source_revisions=decision_evidence["source_revisions"],
        provider_health=decision_evidence["provider_health"],
    )
    if dict(decision_evidence) != rebuilt:
        raise ValueError(
            "decision_evidence packet_ref or canonical payload does not match"
        )
    compact_token(rebuilt["packet_ref"], field="decision_evidence.packet_ref")
    return rebuilt


def _fallback_planning(
    *,
    goal_id: str,
    proposal_id: str,
    inventory_ref: str,
    decision_evidence_ref: str,
    observed_at: str,
    target_window_size: int,
    max_moved_items: int,
    max_rank_displacement: int,
    protected_material_refs: tuple[str, ...],
    policy_status: str,
    blocker: str,
) -> MaterialDecisionPlanning:
    proposal = build_material_rerank_proposal(
        goal_id=goal_id,
        proposal_id=proposal_id,
        inventory_ref=inventory_ref,
        decision_evidence_ref=decision_evidence_ref,
        observed_at=observed_at,
        target_window_size=target_window_size,
        max_moved_items=max_moved_items,
        max_rank_displacement=max_rank_displacement,
        protected_material_refs=protected_material_refs,
        no_change_reason=(
            "Decision policy did not produce a validated delta; preserve the "
            "current material order."
        ),
    )
    return MaterialDecisionPlanning(
        rerank_proposal=proposal,
        explore_intent=None,
        policy_status=policy_status,
        readiness_blockers=(blocker,),
    )


def _validate_action_evidence_refs(
    *,
    decision_evidence: Mapping[str, Any],
    actions: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
) -> None:
    """Reject known refs whose fact or exact source revision is not current.

    Evidence refs remain opaque when they do not identify a record in this
    packet. For recognized packet refs, freshness and source-revision linkage
    are checked before a caller policy's action is accepted.
    """

    source_revision_statuses: dict[tuple[str, str], list[bool]] = {}
    source_pairs: dict[str, set[tuple[str, str]]] = {}
    revision_pairs: dict[str, set[tuple[str, str]]] = {}
    supported_source_revisions: set[tuple[str, str]] = set()
    known_source_refs: set[str] = set()
    known_revision_refs: set[str] = set()
    recognized_statuses: dict[str, list[bool]] = {}

    def record(ref: Any, current: bool) -> None:
        if isinstance(ref, str) and ref:
            recognized_statuses.setdefault(ref, []).append(current)

    source_revisions = decision_evidence["source_revisions"]
    for source_revision in source_revisions:
        source_ref = source_revision["source_ref"]
        revision = source_revision["revision"]
        current = source_revision["freshness"] in _CURRENT_FRESHNESS_VALUES
        source_revision_statuses.setdefault((source_ref, revision), []).append(
            current
        )
        pair = (source_ref, revision)
        source_pairs.setdefault(source_ref, set()).add(pair)
        revision_pairs.setdefault(revision, set()).add(pair)
        known_source_refs.add(source_ref)
        known_revision_refs.add(revision)

    def source_revision_is_current(source_ref: Any, revision: Any) -> bool:
        if not isinstance(source_ref, str) or not isinstance(revision, str):
            return False
        statuses = source_revision_statuses.get((source_ref, revision), ())
        return bool(statuses) and all(statuses)

    def record_supported_revision(source_ref: Any, revision: Any) -> bool:
        pair = (source_ref, revision)
        if not source_revision_is_current(*pair):
            return False
        supported_source_revisions.add(pair)
        return True

    for fact in decision_evidence["changed_facts"]:
        fact_is_current = (
            fact.get("freshness") in _CURRENT_FRESHNESS_VALUES
            and record_supported_revision(
                fact.get("source_ref"), fact.get("source_revision")
            )
        )
        record(
            fact["fact_id"],
            fact_is_current,
        )

    for claim in decision_evidence["recalled_claims"]:
        claim_is_current = (
            claim.get("exact_read_verified") is True
            and record_supported_revision(
                claim.get("source_ref"), claim.get("source_revision")
            )
        )
        record(
            claim["claim_id"],
            claim_is_current,
        )

    for claim in decision_evidence["stale_or_rejected_claims"]:
        record(claim["claim_id"], False)

    for source_ref in known_source_refs:
        pairs = source_pairs[source_ref]
        record(
            source_ref,
            all(
                source_revision_is_current(*pair)
                and pair in supported_source_revisions
                for pair in pairs
            ),
        )
    for revision in known_revision_refs:
        pairs = revision_pairs[revision]
        record(
            revision,
            all(
                source_revision_is_current(*pair)
                and pair in supported_source_revisions
                for pair in pairs
            ),
        )

    for action_field, records in actions:
        for index, action in enumerate(records):
            for evidence_ref in action["evidence_refs"]:
                statuses = recognized_statuses.get(evidence_ref)
                if statuses is not None and not all(statuses):
                    raise ValueError(
                        f"{action_field}[{index}] references stale or unverified evidence"
                    )


def plan_material_decision_actions(
    *,
    policy: MaterialDecisionPolicy,
    policy_id: str,
    goal_id: str,
    proposal_id: str,
    explore_intent_id: str,
    inventory_ref: str,
    decision_evidence: Mapping[str, Any],
    observed_at: str,
    target_window_size: int,
    max_moved_items: int,
    max_rank_displacement: int,
    protected_material_refs: Sequence[str] | None,
    max_explore_topics: int,
    max_provider_calls: int,
    max_new_candidates: int,
    explore_stop_condition: str,
) -> MaterialDecisionPlanning:
    """Invoke one policy and validate bounded rerank and Explore outputs."""

    normalized_goal_id = compact_token(goal_id, field="goal_id")
    expected_policy_id = compact_token(policy_id, field="policy_id")
    actual_policy_id = compact_token(
        getattr(policy, "policy_id", ""),
        field="policy.policy_id",
    )
    if actual_policy_id != expected_policy_id:
        raise ValueError("material decision policy identity does not match")
    evidence_snapshot = _validated_decision_evidence(
        decision_evidence,
        goal_id=normalized_goal_id,
    )
    evidence_ref = evidence_snapshot["packet_ref"]

    window = positive_int(target_window_size, field="target_window_size")
    max_moved = positive_int(max_moved_items, field="max_moved_items")
    max_displacement = positive_int(
        max_rank_displacement,
        field="max_rank_displacement",
    )
    protected = tuple(
        token_list(
            protected_material_refs,
            field="protected_material_refs",
        )
    )
    max_topics = positive_int(
        max_explore_topics,
        field="max_explore_topics",
    )
    max_calls = positive_int(
        max_provider_calls,
        field="max_provider_calls",
    )
    max_candidates = positive_int(
        max_new_candidates,
        field="max_new_candidates",
    )
    iso_timestamp(observed_at, field="observed_at")
    compact_text(explore_stop_condition, field="explore_stop_condition")

    try:
        result = policy.evaluate(
            goal_id=normalized_goal_id,
            decision_evidence=deepcopy(evidence_snapshot),
            inventory_ref=inventory_ref,
            observed_at=observed_at,
            target_window_size=window,
            max_moved_items=max_moved,
            max_rank_displacement=max_displacement,
            protected_material_refs=protected,
            max_explore_topics=max_topics,
            max_provider_calls=max_calls,
            max_new_candidates=max_candidates,
        )
    except Exception:
        return _fallback_planning(
            goal_id=normalized_goal_id,
            proposal_id=proposal_id,
            inventory_ref=inventory_ref,
            decision_evidence_ref=evidence_ref,
            observed_at=observed_at,
            target_window_size=window,
            max_moved_items=max_moved,
            max_rank_displacement=max_displacement,
            protected_material_refs=protected,
            policy_status="unavailable",
            blocker="decision_policy_unavailable",
        )

    if not isinstance(result, MaterialDecisionPolicyResult):
        return _fallback_planning(
            goal_id=normalized_goal_id,
            proposal_id=proposal_id,
            inventory_ref=inventory_ref,
            decision_evidence_ref=evidence_ref,
            observed_at=observed_at,
            target_window_size=window,
            max_moved_items=max_moved,
            max_rank_displacement=max_displacement,
            protected_material_refs=protected,
            policy_status="invalid",
            blocker="decision_policy_contract_invalid",
        )

    try:
        proposal = build_material_rerank_proposal(
            goal_id=normalized_goal_id,
            proposal_id=proposal_id,
            inventory_ref=inventory_ref,
            decision_evidence_ref=evidence_ref,
            observed_at=observed_at,
            target_window_size=window,
            max_moved_items=max_moved,
            max_rank_displacement=max_displacement,
            moves=result.moves,
            protected_material_refs=protected,
            no_change_reason=result.no_change_reason,
        )
        explore_intent = (
            build_material_explore_intent(
                goal_id=normalized_goal_id,
                intent_id=explore_intent_id,
                inventory_ref=inventory_ref,
                decision_evidence_ref=evidence_ref,
                policy_ref=expected_policy_id,
                observed_at=observed_at,
                max_explore_topics=max_topics,
                max_provider_calls=max_calls,
                max_new_candidates=max_candidates,
                stop_condition=explore_stop_condition,
                topics=result.explore_topics,
            )
            if result.explore_topics
            else None
        )
        _validate_action_evidence_refs(
            decision_evidence=evidence_snapshot,
            actions=(
                ("moves", proposal["moves"]),
                (
                    "topics",
                    explore_intent["topics"] if explore_intent is not None else (),
                ),
            ),
        )
    except (TypeError, ValueError):
        return _fallback_planning(
            goal_id=normalized_goal_id,
            proposal_id=proposal_id,
            inventory_ref=inventory_ref,
            decision_evidence_ref=evidence_ref,
            observed_at=observed_at,
            target_window_size=window,
            max_moved_items=max_moved,
            max_rank_displacement=max_displacement,
            protected_material_refs=protected,
            policy_status="invalid",
            blocker="decision_policy_contract_invalid",
        )

    return MaterialDecisionPlanning(
        rerank_proposal=proposal,
        explore_intent=explore_intent,
        policy_status="ready",
    )
