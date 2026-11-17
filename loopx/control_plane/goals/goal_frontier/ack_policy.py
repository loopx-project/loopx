from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TypeGuard, cast

from ...runtime.time import parse_timestamp
from ...todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_replan_obligation_id,
    replan_successor_semantic_binding,
)
from ...todos.todo_semantics import todo_item_is_actionable_open, todo_item_task_class
from ...todos.succession_warning import TODO_SUCCESSION_WARNING_REASON_CODE
from ...work_items.autonomous_replan_ack import (
    autonomous_replan_ack_matches_agent,
    normalize_projected_autonomous_replan_ack,
)
from ...work_items.progress_observation import (
    replan_obligation_trigger_checkpoints,
    replan_obligation_trigger_kinds,
    replan_successor_transition_allowed,
    required_semantic_outcomes,
)
from ...work_items.autonomous_replan_obligation import ensure_replan_novelty_policy
from ...work_items.replan_semantics_codec import project_replan_semantics
from .long_todo_chain import (
    LONG_TODO_CHAIN_TRIGGER,
    long_todo_chain_successor_checkpoints,
)


Payload = dict[str, object]
PayloadView = Mapping[str, object]


def autonomous_replan_ack_has_frontier_delta(ack: PayloadView | None) -> TypeGuard[Payload]:
    if not isinstance(ack, dict) or ack.get("recorded") is not True:
        return False
    semantic_delta = ack.get("semantic_delta")
    return bool(
        isinstance(semantic_delta, dict)
        and semantic_delta.get("accepted") is True
        and semantic_delta.get("outcomes")
    )


def autonomous_replan_ack_satisfies_obligation(
    ack: PayloadView | None,
    *,
    replan_obligation: PayloadView | None,
    acceptance_gaps: Sequence[PayloadView] | None,
    todo_succession_gap_open: bool = False,
    stride_strategy: str = "off",
) -> bool:
    """Reject ACKs that miss the obligation's typed semantic outcome."""

    if not autonomous_replan_ack_has_frontier_delta(ack):
        return False
    obligation = replan_obligation or {}
    if ack.get("source") == "todo_replan_successor_transition" and not replan_successor_transition_allowed(
        obligation, stride_strategy=stride_strategy,
    ):
        return False
    semantic_delta = ack.get("semantic_delta") if isinstance(ack, dict) else {}
    obligation_id = str(obligation.get("obligation_id") or "").strip()
    acknowledged_obligation_id = str(
        semantic_delta.get("obligation_id")
        if isinstance(semantic_delta, dict)
        else ""
    ).strip()
    if not obligation_id or acknowledged_obligation_id != obligation_id:
        return False
    raw_outcomes = semantic_delta.get("outcomes") if isinstance(semantic_delta, dict) else None
    outcomes = {
        str(item or "").strip()
        for item in (raw_outcomes if isinstance(raw_outcomes, list) else ())
        if str(item or "").strip()
    }
    required = set(required_semantic_outcomes(obligation))
    # Other completed work keeps its own succession duty. It must not veto an
    # exact accepted receipt for an independent monitor or review obligation.
    if (
        todo_succession_gap_open
        and TODO_SUCCESSION_WARNING_REASON_CODE
        in replan_obligation_trigger_kinds(obligation)
    ):
        outcomes.intersection_update({"new_runnable_successor"})
    return bool(outcomes & required)


def replan_successor_transition_ack(
    agent_todo_summary: PayloadView | None,
    *,
    agent_id: str | None,
    replan_obligation: PayloadView | None,
    agent_todo_items: Iterable[PayloadView] | None = None,
    stride_strategy: str = "off",
) -> Payload | None:
    """Project an exact runnable-successor Todo as the replan receipt.

    The Todo row is the durable fact.  No prose classification and no second
    ACK command are involved.  A receipt applies only while the bound Todo is
    still open, advancement-class, runnable, and owned by the same agent.
    """

    obligation = replan_obligation or {}
    obligation_id = normalize_todo_replan_obligation_id(
        obligation.get("obligation_id")
    )
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not obligation_id or not safe_agent_id:
        return None
    if agent_todo_items is None:
        raw_executable = (
            agent_todo_summary.get("first_executable_items")
            if isinstance(agent_todo_summary, dict)
            else None
        )
        source_items: list[Payload] = [
            item for item in (raw_executable if isinstance(raw_executable, list) else ())
            if isinstance(item, dict)
        ]
    else:
        source_items = [
            cast(Payload, item) for item in agent_todo_items if isinstance(item, dict)
        ]
    trigger_kinds = replan_obligation_trigger_kinds(obligation)
    candidates = [item for item in source_items
        if todo_item_is_actionable_open(item)
        and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        and normalize_todo_claimed_by(item.get("claimed_by")) == safe_agent_id
        and normalize_todo_replan_obligation_id(item.get("replan_obligation_id"))
        and normalize_todo_id(item.get("todo_id"))
        and replan_successor_semantic_binding(action_kind=item.get("action_kind"),
            target_key=item.get("target_key"), explore_result_node_refs=item.get("explore_result_node_refs"))]
    if not candidates:
        return None
    trigger_checkpoints: list[dict[str, str]] | None = None
    eligible_ids = {item["todo_id"] for item in candidates if item["replan_obligation_id"] == obligation_id}
    if LONG_TODO_CHAIN_TRIGGER in trigger_kinds:
        raw_triggers = obligation.get("triggers")
        triggers_arg = [item for item in raw_triggers if isinstance(item, dict)] if isinstance(raw_triggers, list) else []
        source_checkpoint = long_todo_chain_successor_checkpoints(
            source_items,
            agent_id=safe_agent_id,
            triggers=triggers_arg,
            obligation_id=obligation_id,
            candidates=[{"todo_id": item["todo_id"], "updated_at": item.get("updated_at"),
                "origin_obligation_id": item["replan_obligation_id"]} for item in candidates],
            frontier_revision_index=(agent_todo_summary or {}).get(
                "advancement_frontier_revision_index"
            ),
        )
        if source_checkpoint is None:
            return None
        trigger_checkpoints = source_checkpoint["trigger_checkpoints"]
        eligible_ids = set()
        origins = {item["todo_id"]: item["replan_obligation_id"] for item in candidates}
        for binding in source_checkpoint["bindings"]:
            if binding["kind"] == "exact":
                eligible_ids.add(binding["todo_id"])
            elif binding["kind"] == "predecessor":
                prior = ensure_replan_novelty_policy({**obligation, "triggers": [
                    {**trigger, "frontier_revision": binding["frontier_revision"],
                     "obligation_identity_revision": binding["obligation_identity_revision"]}
                    for trigger in triggers_arg]})
                if prior["obligation_id"] == origins[binding["todo_id"]]:
                    eligible_ids.add(binding["todo_id"])
    successor = next((item for item in candidates if item["todo_id"] in eligible_ids), None)
    if successor is None:
        return None
    successor_todo_id = normalize_todo_id(successor.get("todo_id"))
    successor_binding = replan_successor_semantic_binding(
        action_kind=successor.get("action_kind"),
        target_key=successor.get("target_key"),
        explore_result_node_refs=successor.get("explore_result_node_refs"),
    )
    if successor_binding is None:
        return None
    if not replan_successor_transition_allowed(
        obligation, stride_strategy=stride_strategy,
    ):
        return None
    if trigger_checkpoints is None:
        trigger_checkpoints = replan_obligation_trigger_checkpoints(obligation)
    semantic_delta = {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": True,
        "outcomes": ["new_runnable_successor"],
        "satisfying_outcomes": ["new_runnable_successor"],
        "required_any_of": required_semantic_outcomes(obligation),
        "trigger_kinds": trigger_kinds,
        "trigger_checkpoints": trigger_checkpoints,
        "obligation_id": obligation_id,
        "successor_todo_id": successor_todo_id,
        "successor_origin_obligation_id": successor["replan_obligation_id"],
        "successor_binding": successor_binding,
        "reason": (
            "an exact or source-proven predecessor Todo transition created a runnable "
            "successor"
        ),
    }
    ack: Payload = {
        "schema_version": "autonomous_replan_ack_v0",
        "recorded": True,
        "source": "todo_replan_successor_transition",
        "agent_id": safe_agent_id,
        "generated_at": successor.get("updated_at"),
        "semantic_delta": semantic_delta,
    }
    frontier_identity = str(
        obligation.get("frontier_identity") or ""
    ).strip()
    if frontier_identity:
        ack["frontier_identity"] = frontier_identity
    return ack


def attach_rejected_replan_feedback(
    obligation: PayloadView | None, feedback: PayloadView | None,
) -> Payload | None:
    """Preserve the existing rejected-claim readback without changing the duty."""
    if not obligation or not feedback:
        return dict(obligation) if isinstance(obligation, dict) else None
    updated: Payload = dict(obligation)
    updated["replan_ack_feedback"] = feedback
    raw_claims = feedback.get("rejected_claims")
    first_claim = next((item for item in (raw_claims if isinstance(raw_claims, list) else ())
                        if isinstance(item, dict)), None)
    if first_claim:
        updated["recommended_action"] = (
            "previous replan ACK was rejected: "
            f"{first_claim.get('kind')}: {first_claim.get('reason')}; "
            + str(updated.get("recommended_action") or "replan again"))
    return updated


def _ack_semantic_delta(latest_replan_ack: PayloadView | None) -> PayloadView:
    if not isinstance(latest_replan_ack, Mapping):
        return {}
    semantic_delta = latest_replan_ack.get("semantic_delta")
    return cast(PayloadView, semantic_delta) if isinstance(semantic_delta, Mapping) else {}


def projected_autonomous_replan_ack_for_agent(
    item: PayloadView,
    project_asset: PayloadView | None,
    *,
    agent_id: str | None,
) -> Payload | None:
    """Return the current projected replan ACK when it belongs to this agent."""

    asset = project_asset if isinstance(project_asset, dict) else {}
    for candidate in (item.get("autonomous_replan_ack"), asset.get("autonomous_replan_ack")):
        if not isinstance(candidate, dict):
            continue
        normalized = normalize_projected_autonomous_replan_ack(candidate)
        if autonomous_replan_ack_matches_agent(normalized, agent_id=agent_id):
            return cast(Payload, normalized)
    return None


def _replan_evidence_acknowledged(
    items: Sequence[Mapping[str, object]],
    latest_replan_ack: Mapping[str, object] | None,
    *,
    time_key: str,
) -> bool:
    """Return true when a valid replan ack postdates the newest evidence item.

    A gap that the agent already acknowledged with a repair delta must not
    re-derive an endless replan obligation: without this, any stale gap turns
    quota into a permanent run-now loop that no acknowledgement can settle.
    """

    if not items or not isinstance(latest_replan_ack, Mapping):
        return False
    ack_time = parse_timestamp(latest_replan_ack.get("generated_at"))
    if ack_time is None:
        return False
    newest = max(
        (
            timestamp
            for item in items
            if isinstance(item, dict)
            if (timestamp := parse_timestamp(item.get(time_key))) is not None
        ),
        default=None,
    )
    return newest is not None and ack_time >= newest


def _succession_gap_acknowledged(
    gap_items: Sequence[Mapping[str, object]],
    latest_replan_ack: Mapping[str, object] | None,
) -> bool:
    """Return true when a valid replan ack postdates the newest succession gap."""

    outcomes = _ack_semantic_delta(latest_replan_ack).get("outcomes")
    if "new_runnable_successor" not in set(outcomes if isinstance(outcomes, list) else ()):
        return False
    return _replan_evidence_acknowledged(
        gap_items,
        latest_replan_ack,
        time_key="completed_at",
    )


def _vision_gap_acknowledged(
    acceptance_gaps: Sequence[PayloadView],
    latest_replan_ack: PayloadView | None,
) -> bool:
    """Delegate exact acceptance holds and ordinary evidence freshness to TS."""

    if not acceptance_gaps or not isinstance(latest_replan_ack, dict):
        return False
    return project_replan_semantics({
        "operation": "vision_ack",
        "acceptance_gaps": list(acceptance_gaps),
        "ack": latest_replan_ack,
    })["acknowledged"] is True


def _acknowledged_replan_obligation_id(
    latest_replan_ack: Mapping[str, object] | None,
) -> str | None:
    """Return the exact obligation generation closed by the latest ACK."""

    return normalize_todo_replan_obligation_id(
        _ack_semantic_delta(latest_replan_ack).get("obligation_id")
    )
