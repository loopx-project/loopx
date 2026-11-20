"""Derive scoped recovery evidence from canonical acceptance associations.

This projection never grants a binding. TypeScript replan semantics owns its
legal discharge outcomes; the canonical acceptance owner still guards execution.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import cast

from ...agents.agent_scope import agent_scope_item_claimed_by_agent_or_unclaimed
from ...agents.profile import agent_profile_requires_vision
from ...effect_runtime import CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS, effect_runtime_result
from ...todos.contract import normalize_todo_replan_obligation_id
from ..acceptance_readback import configured_checks

Payload = dict[str, object]
PayloadView = Mapping[str, object]

GOAL_ACCEPTANCE_HOLD_TRIGGERS = frozenset({"goal_acceptance_stale", "goal_acceptance_unbound"})
VISION_PROFILE_MISSING_TRIGGER = "required_agent_vision_missing"


def _as_mapping(value: object) -> PayloadView:
    return cast(PayloadView, value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def acceptance_gaps_from_agent_profile_requirement(
    agent_profile: PayloadView | None,
    *,
    agent_id: str | None,
    agent_vision: PayloadView | None,
    missing_checkpoint: PayloadView | None,
) -> list[Payload]:
    """Require a vision baseline for registered long-lived peer lanes."""

    if (
        not agent_id
        or not agent_profile_requires_vision(agent_profile)
        or isinstance(agent_vision, dict)
        or isinstance(missing_checkpoint, dict)
    ):
        return []
    return [
        {
            "kind": VISION_PROFILE_MISSING_TRIGGER,
            "source": "agent_profile",
            "agent_id": agent_id,
            "replan_trigger_summary": (
                "the registered long-lived agent lane requires a persisted vision "
                "baseline before ordinary delivery or monitor-only quiet wait"
            ),
            "acceptance_summary": (
                "Write a bounded agent vision with objective, acceptance evidence, "
                "advancement policy, and replan trigger; or explicitly mark the "
                "profile vision requirement optional."
            ),
            "advancement_policy": "repeat_until_closed",
        }
    ]


def _hold_checkpoint_revision(identity: list[object]) -> str:
    return hashlib.sha256(json.dumps(
        identity, ensure_ascii=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def acceptance_gaps_from_held_goal_binding(
    agent_todo_summary: PayloadView | None,
    source_items: Sequence[PayloadView] | None,
    *,
    agent_id: str | None,
) -> list[Payload]:
    """Route missing or stale acceptance through this agent's replan lane.

    This is a read-only trigger, not an acceptance rebind. Other runnable work
    remains selectable; the frontier rule schedules a replan only when no
    advancement Todo can be selected for this agent.
    """

    if not agent_id or not isinstance(agent_todo_summary, dict):
        return []
    contract = agent_todo_summary.get("goal_acceptance_contract")
    if not isinstance(contract, dict) or configured_checks(contract) is None:
        return []
    held_states = {
        task.get("todo_id"): task.get("state")
        for task in _as_list(contract.get("tasks"))
        if isinstance(task, dict)
        and task.get("state") in {"stale", "unbound"}
        and task.get("applicable") is True
    }
    gaps: list[Payload] = []
    for item in source_items or []:
        if (
            not isinstance(item, dict)
            or item.get("todo_id") not in held_states
            or item.get("role") != "agent"
            or item.get("status") not in {"open", "blocked"}
            or not agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
        ):
            continue
        todo_id = str(item["todo_id"])
        state = held_states[todo_id]
        kind = f"goal_acceptance_{state}"
        generated_at = item.get("updated_at") or item.get("created_at")
        # The hold belongs to an owner-confirmed contract revision. Editing
        # unconfirmed work, its display text or a review note does not create
        # another owner-review opportunity. The current timestamp remains
        # useful readback, but only a real contract revision changes this basis.
        owner_basis = [contract.get("digest"), contract.get("revision"), state]
        frontier_revision = _hold_checkpoint_revision([todo_id, *owner_basis])
        gap: Payload = {
            "kind": kind,
            "source": "goal_acceptance_contract",
            "agent_id": agent_id,
            "reason_code": kind,
            "vision_todo_ids": [todo_id],
            "frontier_revision": frontier_revision,
            # Compatibility proof comes from the same current canonical source,
            # not an unqualified historical ACK. Never rewrite old receipts.
            "legacy_frontier_revision": _hold_checkpoint_revision([todo_id, generated_at, *owner_basis]),
            "replan_trigger_summary": f"The acceptance association for {todo_id} is {state}.",
            "acceptance_summary": "Preserve the owner-confirmed criteria and the original Turn identity.",
            "resolution_hint": (
                f"Inspect {todo_id}, its acceptance binding and the owner's contract scope; "
                "a local validation contract must not unintentionally hold independent work. "
                "Prepare a scope/binding correction for the authorized owner when needed; "
                + ("restore the exact prior text/wait using todo update with --update-operation-id "
                   "and --update-expected-provider-revision after releasing any active lease; "
                   "if the prior declaration is unknown or cannot match, request owner rebind, " if state == "stale" else
                   "prepare the missing association for owner review, ") +
                "or record an evidence-linked path delta and continue via an eligible "
                "successor, or record a concrete blocker for the required owner confirmation. "
                "never rebind or settle a different Todo under the original Turn."
            ),
        }
        if isinstance(generated_at, str):
            gap["generated_at"] = generated_at
        gaps.append(gap)
        if len(gaps) == 3:
            break
    return gaps


def acceptance_gaps_from_outcomes(agent_todo_summary: PayloadView | None, *, agent_id: str | None) -> list[Payload]:
    """Transport the typed owner's scoped coverage gaps to existing replan."""
    contract = _as_mapping((agent_todo_summary or {}).get("goal_acceptance_contract"))
    declarations = _as_mapping(contract.get("outcome_requirements"))
    objective = _as_mapping(contract.get("goal_objective"))
    return [
        {str(k): v for k, v in gap.items()}
        for gap in [*_as_list(declarations.get("acceptance_gaps")), *_as_list(objective.get("acceptance_gaps"))]
        if isinstance(gap, dict) and agent_id and gap.get("agent_id") == agent_id
    ]


def project_legacy_acceptance_hold_ack(
    ack: Payload | None,
    *,
    acceptance_gaps: Sequence[PayloadView],
    goal_id: str,
    agent_id: str | None,
    runtime_root: str | None,
) -> Payload | None:
    """Read an old ACK against its exact canonical source, without rewriting it.

    The old checkpoint included the Todo timestamp. Its run's retained source
    anchor proves those bytes even after a later display edit. The existing
    source owner verifies store lineage, cursor and revision in one bounded
    transaction read. Equal current checkpoint hashes additionally require the
    same Todo, owner contract digest/revision and hold state. No historical
    source, unknown checkpoint or changed owner basis can acquire this mapping.
    """
    delta = _as_mapping((ack or {}).get("semantic_delta"))
    checkpoints = delta.get("trigger_checkpoints")
    current = {(gap.get("kind"), gap.get("frontier_revision"))
               for gap in acceptance_gaps if gap.get("kind") in GOAL_ACCEPTANCE_HOLD_TRIGGERS}
    if (not current or not isinstance(checkpoints, list) or not runtime_root or
            not isinstance(ack, dict) or
            ack.get("recorded") is not True or delta.get("accepted") is not True or
            ack.get("agent_id") != agent_id or
            not normalize_todo_replan_obligation_id(delta.get("obligation_id"))):
        return ack
    legacy = [checkpoint for checkpoint in checkpoints if isinstance(checkpoint, dict) and
              checkpoint.get("kind") in GOAL_ACCEPTANCE_HOLD_TRIGGERS and
              (checkpoint.get("kind"), checkpoint.get("frontier_revision")) not in current]
    anchor = ack.get("todo_source")
    if not legacy or not isinstance(anchor, dict):
        return ack
    source = effect_runtime_result("coordination.local_authority.todo_source", {
        "schema_version": "loopx_local_coordination_todo_source_request_v0",
        "runtime_root": runtime_root, "goal_id": goal_id, "source": anchor,
    }, timeout=CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS, large_local_snapshot=True)
    if (source.get("status") != "loaded" or source.get("goal_id") != goal_id or
            source.get("source") != anchor or source.get("decision_read_from_provider") is not True or
            source.get("legacy_fallback_used") is not False):
        return ack
    raw_todos = source.get("todos")
    todos_seq = [item for item in raw_todos if isinstance(item, dict)] if isinstance(raw_todos, list) else None
    raw_contract = source.get("goal_acceptance_contract")
    old = {(gap["kind"], gap["frontier_revision"]): gap["legacy_frontier_revision"]
           for gap in acceptance_gaps_from_held_goal_binding(
               {"goal_acceptance_contract": raw_contract},
               todos_seq, agent_id=agent_id,
           )}
    verified = {(kind, previous): revision for (kind, revision), previous in old.items()
                if (kind, revision) in current}
    projected = [{**checkpoint, "frontier_revision": verified.get(
        (checkpoint.get("kind"), checkpoint.get("frontier_revision")), checkpoint.get("frontier_revision"),
    )} if isinstance(checkpoint, dict) else checkpoint for checkpoint in checkpoints]
    # This ephemeral projection feeds the existing exact typed ACK comparison.
    # Keep the original obligation identity, outcomes, durable record and debit.
    return {**ack, "semantic_delta": {**delta, "trigger_checkpoints": projected}}
