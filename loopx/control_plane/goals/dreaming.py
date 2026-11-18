from __future__ import annotations

from typing import Any, Callable, Optional


PublicSafeText = Callable[..., Optional[str]]
PublicSafeList = Callable[..., list[str]]
NormalizeOperatorQuestion = Callable[..., str]


def compact_server_planning_contract(
    value: Any,
    *,
    public_safe_compact_text: PublicSafeText,
    public_safe_compact_list: PublicSafeList,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for field in ("schema_version", "lane", "authority"):
        text = public_safe_compact_text(value.get(field), limit=120)
        if text:
            compact[field] = text
    for field in (
        "may_rank_candidate_todos",
        "may_suggest_evidence_probes",
        "may_emit_refactor_warnings",
        "may_execute_protected_actions",
        "may_read_private_material",
        "may_mutate_active_state",
        "may_append_delivery_history",
        "may_spend_delivery_quota",
        "promotion_required",
    ):
        if isinstance(value.get(field), bool):
            compact[field] = value[field]
    for field in ("promotion_requirements", "allowed_outputs", "forbidden_outputs"):
        items = public_safe_compact_list(value.get(field), limit=8)
        if items:
            compact[field] = items
    return compact


def compact_dreaming_proposal(
    run: dict[str, Any] | None,
    *,
    dreaming_advisory_classifications: set[str],
    public_safe_compact_text: PublicSafeText,
    public_safe_compact_list: PublicSafeList,
) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    classification = str(run.get("classification") or "")
    if classification not in dreaming_advisory_classifications:
        return None
    raw_dreaming = run.get("dreaming")
    raw = raw_dreaming if isinstance(raw_dreaming, dict) else {}
    proposal: dict[str, Any] = {
        "schema_version": "dreaming_proposal_v0",
        "classification": classification,
        "advisory": True,
        "promoted_to_delivery": False,
        "execution_allowed": False,
        "delivery_spend_allowed": False,
    }
    for key in ("proposal_id", "lane", "evidence_window", "proposal_type", "confidence"):
        value = public_safe_compact_text(raw.get(key), limit=80)
        if value:
            proposal[key] = value
    server_planning_contract = compact_server_planning_contract(
        raw.get("server_planning_contract"),
        public_safe_compact_text=public_safe_compact_text,
        public_safe_compact_list=public_safe_compact_list,
    )
    if server_planning_contract:
        proposal["server_planning_contract"] = server_planning_contract
    if raw.get("requires_project_controller") is not None:
        proposal["requires_project_controller"] = bool(raw.get("requires_project_controller"))
    question = public_safe_compact_text(
        run.get("operator_question") or raw.get("operator_question"),
        limit=220,
    )
    if question:
        proposal["operator_question"] = question
    return proposal


def compact_dreaming_lane_badge(
    proposal: dict[str, Any] | None,
    *,
    public_safe_compact_text: PublicSafeText,
) -> dict[str, Any] | None:
    if not isinstance(proposal, dict):
        return None
    classification = public_safe_compact_text(proposal.get("classification"), limit=80)
    badge: dict[str, Any] = {
        "schema_version": "dreaming_lane_badge_v0",
        "lane": "dreaming",
        "label": "Dreaming",
        "advisory": bool(proposal.get("advisory", True)),
        "interrupts_delivery": False,
        "review_required": True,
        "execution_allowed": False,
        "delivery_spend_allowed": False,
        "promoted_to_delivery": False,
    }
    if classification:
        badge["status"] = classification
    for field in ("proposal_id", "proposal_type", "confidence", "evidence_window"):
        value = public_safe_compact_text(proposal.get(field), limit=80)
        if value:
            badge[field] = value
    server_contract = proposal.get("server_planning_contract")
    if isinstance(server_contract, dict):
        lane = public_safe_compact_text(server_contract.get("lane"), limit=80)
        authority = public_safe_compact_text(server_contract.get("authority"), limit=120)
        if lane or authority:
            badge["server_planning"] = {
                key: value
                for key, value in {
                    "lane": lane,
                    "authority": authority,
                }.items()
                if value
            }
    return badge


def dreaming_attention_fields(
    run: dict[str, Any] | None,
    *,
    dreaming_advisory_classifications: set[str],
    public_safe_compact_text: PublicSafeText,
    public_safe_compact_list: PublicSafeList,
    normalize_operator_question: NormalizeOperatorQuestion,
) -> dict[str, Any]:
    proposal = compact_dreaming_proposal(
        run,
        dreaming_advisory_classifications=dreaming_advisory_classifications,
        public_safe_compact_text=public_safe_compact_text,
        public_safe_compact_list=public_safe_compact_list,
    )
    if not proposal:
        return {}
    fields: dict[str, Any] = {"dreaming_proposal": proposal}
    question = proposal.get("operator_question")
    if question:
        fields["operator_question"] = normalize_operator_question(
            str(question),
            goal_id=str((run or {}).get("goal_id") or ""),
            gate="dreaming_proposal_review",
        )
    return fields
