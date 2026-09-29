"""Materialize governed provider proposals through LoopX-owned Todo APIs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from enum import StrEnum
from pathlib import Path
from typing import Any

from ...todos import (
    add_goal_todo,
    complete_goal_todo,
    list_goal_todos,
    update_goal_todo,
)
from ..coordination.coordination_state_contract_generated import COORDINATION_STATE_CONTRACT
from ..runtime.public_safety import validate_public_safe_value
from ..content_digest import ENVELOPED_SHA256_PATTERN
from ..todos.contract import (
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_capability_binding_ref,
)

GOVERNED_TRANSITION_RECEIPT_SCHEMA_VERSION = (
    "loopx_governed_transition_proposal_receipt_v0"
)
_RECEIPT_FIELDS = {
    "schema_version",
    "proposal_id",
    "proposal_digest",
    "kind",
    "monitor_key",
    "action",
    "todo_id",
    "status",
    "target_key",
}
# Receipts are persisted in the settlement journal, so the field set stays
# closed and a new field is admitted only as an explicitly bounded addition
# that an older receipt may still omit. `lane_todo_ids` is the readback of a
# team plan: every lane Todo the settlement ensured, not just the first one.
# `lane_settlements` keeps the lane->Todo->acceptance relationship the owner
# confirmed, so a committed plan can still be read as "which lane was meant to
# end on what" after the prose answer is gone.
_OPTIONAL_RECEIPT_FIELDS = {
    "lane_todo_ids",
    "intent_basis",
    "lane_settlements",
    "gap_count",
    "lane_failure",
    "gap_lanes",
    "reason_code",
}
# A team plan the typed owner refused before any lane write. The receipt keeps
# the settlement's verdict so a replay returns the same answer instead of
# retrying; it names no lane identity because none was created. Any other
# commit error still raises: it is not a verdict about the plan.
TEAM_PLAN_FAILURE_REASON_CODES = (
    "team_plan_no_staffable_lane",
    "team_plan_preview_stale",
    "team_plan_basis_missing",
)
_FAILED_RECEIPT_ACTION = "failed"
_LANE_TODO_ID_LIMIT = 8
_LANE_TODO_ID = re.compile(r"^todo_[A-Za-z0-9]{1,40}$")
_LANE_SETTLEMENT_FIELDS = {
    "lane_id",
    "agent_id",
    "priority",
    "disposition",
    "todo_id",
    "acceptance",
}
_LANE_SETTLEMENT_DISPOSITIONS = ("created", "reused")
# A lane write this settlement could not complete. The vocabulary stays typed so
# a reader can act on the failure without parsing prose.
_LANE_FAILURE_REASON_CODES = ("lane_write_failed",)


TransitionCheckpoint = Callable[[list[dict[str, Any]]], None]


class GovernedTransitionSettlementPhase(StrEnum):
    """Turn-settlement phase that owns one admitted Kernel transition."""

    PRE_SETTLEMENT = "pre_settlement"
    POST_SETTLEMENT = "post_settlement"


STEWARD_TEAM_PLAN_PREVIEW_KIND = "steward_team_plan_preview"

# This table is the settlement owner for every proposal kind LoopX can
# materialize. The external-capability result gate (`governed_capability.ts`
# and the extension manifest's `transition_contract.proposal_kinds`) admits
# only the monitor kinds, so no provider result carries a team plan yet; the
# team-plan route is kept so that admitting the kind, a separate provider
# contract change, inherits the basis requirement: the journal binds a basis
# for an operation that declares the kind and `_apply_team_plan` settles only
# against it.
_SETTLEMENT_PHASE_BY_PROPOSAL_KIND = {
    "continuous_monitor_upsert": GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
    "continuous_monitor_complete": GovernedTransitionSettlementPhase.POST_SETTLEMENT,
    STEWARD_TEAM_PLAN_PREVIEW_KIND: GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
}


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): deepcopy(item) for key, item in value.items()}


def validate_governed_transition_receipts(
    value: object,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(
            "governed transition proposal receipts must contain at most 64 items"
        )
    receipts: list[dict[str, Any]] = []
    proposal_ids: set[str] = set()
    for index, raw in enumerate(value):
        receipt = _mapping(raw, f"governed transition receipt[{index}]")
        # An older receipt may omit the bounded readback field; nothing else may
        # be added, so a receipt can never carry a field it did not mean to.
        if not _RECEIPT_FIELDS <= set(receipt) <= (
            _RECEIPT_FIELDS | _OPTIONAL_RECEIPT_FIELDS
        ):
            raise ValueError("governed transition proposal receipt fields are invalid")
        if receipt.get("schema_version") != GOVERNED_TRANSITION_RECEIPT_SCHEMA_VERSION:
            raise ValueError("governed transition proposal receipt schema is invalid")
        proposal_id = str(receipt.get("proposal_id") or "")
        if not proposal_id or proposal_id in proposal_ids:
            raise ValueError("governed transition proposal receipt identity is invalid")
        proposal_ids.add(proposal_id)
        if receipt.get("kind") not in {
            "continuous_monitor_upsert",
            "continuous_monitor_complete",
            STEWARD_TEAM_PLAN_PREVIEW_KIND,
        }:
            raise ValueError("governed transition proposal receipt kind is invalid")
        if not isinstance(receipt.get("proposal_digest"), str) or not receipt["proposal_digest"]:
            raise ValueError(
                "governed transition proposal receipt proposal_digest is invalid"
            )
        if receipt.get("status") == "failed":
            _validate_failed_receipt(receipt)
        elif receipt.get("status") == "committed":
            if "reason_code" in receipt:
                raise ValueError(
                    "governed transition proposal receipt reason_code is invalid"
                )
            for field in ("action", "todo_id"):
                if not isinstance(receipt.get(field), str) or not receipt[field]:
                    raise ValueError(
                        f"governed transition proposal receipt {field} is invalid"
                    )
        else:
            raise ValueError("governed transition proposal receipt status is invalid")
        # A monitor transition is identified by its monitor key, so that key is
        # required there. A team plan is not a monitor and must not invent one,
        # so its key is explicitly absent rather than an empty string.
        monitor_key = receipt.get("monitor_key")
        if receipt.get("kind") == STEWARD_TEAM_PLAN_PREVIEW_KIND:
            if monitor_key is not None:
                raise ValueError(
                    "governed transition proposal receipt monitor_key is invalid"
                )
        elif not isinstance(monitor_key, str) or not monitor_key:
            raise ValueError(
                "governed transition proposal receipt monitor_key is invalid"
            )
        if receipt.get("target_key") is not None and not isinstance(
            receipt.get("target_key"), str
        ):
            raise ValueError(
                "governed transition proposal receipt target_key is invalid"
            )
        lane_todo_ids = receipt.get("lane_todo_ids")
        if lane_todo_ids is not None and (
            not isinstance(lane_todo_ids, list)
            or not 1 <= len(lane_todo_ids) <= _LANE_TODO_ID_LIMIT
            or len(set(lane_todo_ids)) != len(lane_todo_ids)
            or any(
                not isinstance(item, str) or not _LANE_TODO_ID.fullmatch(item)
                for item in lane_todo_ids
            )
        ):
            raise ValueError(
                "governed transition proposal receipt lane_todo_ids is invalid"
            )
        intent_basis = receipt.get("intent_basis")
        if intent_basis is not None and (
            not isinstance(intent_basis, str)
            or not ENVELOPED_SHA256_PATTERN.fullmatch(intent_basis)
        ):
            raise ValueError(
                "governed transition proposal receipt intent_basis is invalid"
            )
        lane_settlements = receipt.get("lane_settlements")
        if lane_settlements is not None:
            normalize_lane_settlements(lane_settlements)
        gap_count = receipt.get("gap_count")
        if gap_count is not None and (
            isinstance(gap_count, bool)
            or not isinstance(gap_count, int)
            or not 1 <= gap_count <= STEWARD_TEAM_PLAN_LANE_LIMIT
        ):
            raise ValueError("governed transition proposal receipt gap_count is invalid")
        gap_lanes = receipt.get("gap_lanes")
        if gap_lanes is not None:
            if not isinstance(gap_lanes, list) or len(gap_lanes) != gap_count:
                raise ValueError("governed transition gap_lanes count is invalid")
            seen_gaps: set[str] = set()
            for gap in gap_lanes:
                if (not isinstance(gap, dict)
                    or set(gap) != {"lane_id", "agent_id", "reason_code"}
                    or not all(isinstance(value, str) and value for value in gap.values())
                    or gap["lane_id"] in seen_gaps
                    or gap["reason_code"] not in STEWARD_TEAM_PLAN_GAP_REASONS + STEWARD_TEAM_PLAN_HOST_GAP_REASONS):
                    raise ValueError("governed transition gap_lanes is invalid")
                seen_gaps.add(gap["lane_id"])
        lane_failure = receipt.get("lane_failure")
        if lane_failure is not None:
            failure = _mapping(lane_failure, "governed transition lane failure")
            if set(failure) != {"lane_id", "reason_code"} or str(
                failure["reason_code"]
            ) not in _LANE_FAILURE_REASON_CODES:
                raise ValueError(
                    "governed transition proposal receipt lane_failure is invalid"
                )
        validate_public_safe_value(receipt, path=f"transition_receipts[{index}]")
        receipts.append(receipt)
    return receipts


def _validate_failed_receipt(receipt: Mapping[str, Any]) -> None:
    """A refused team plan records its typed verdict and no lane identity."""

    if receipt.get("kind") != STEWARD_TEAM_PLAN_PREVIEW_KIND:
        raise ValueError("governed transition proposal receipt status is invalid")
    if receipt.get("reason_code") not in TEAM_PLAN_FAILURE_REASON_CODES:
        raise ValueError("governed transition proposal receipt reason_code is invalid")
    if receipt.get("action") != _FAILED_RECEIPT_ACTION:
        raise ValueError("governed transition proposal receipt action is invalid")
    if receipt.get("todo_id") is not None or receipt.get("target_key") is not None:
        raise ValueError("governed transition proposal receipt todo_id is invalid")
    if set(receipt) - _RECEIPT_FIELDS != {"reason_code"}:
        raise ValueError("governed transition proposal receipt fields are invalid")


def normalize_lane_settlements(value: object) -> list[dict[str, str]]:
    """Read the lane->Todo->acceptance relationship one plan settlement made.

    A confirmed plan carries a lane's acceptance signal as well as the work it
    starts. The Todo owner stores the work, so the acceptance a lane was meant
    to end on is retained beside the Todo identity it became, in the order the
    plan declared its lanes. A lane that was not materialized is absent: the
    settlement reports what exists now, not what was hoped for.
    """

    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= STEWARD_TEAM_PLAN_LANE_LIMIT
    ):
        raise ValueError("governed transition lane settlements are invalid")
    settlements: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _mapping(raw, f"lane_settlement[{index}]")
        if set(item) != _LANE_SETTLEMENT_FIELDS:
            raise ValueError("governed transition lane settlement fields are invalid")
        lane_id = str(item["lane_id"])
        if not lane_id or lane_id in seen:
            raise ValueError("governed transition lane settlement identity is invalid")
        seen.add(lane_id)
        disposition = str(item["disposition"])
        if disposition not in _LANE_SETTLEMENT_DISPOSITIONS:
            raise ValueError(
                "governed transition lane settlement disposition is invalid"
            )
        todo_id = str(item["todo_id"])
        if not _LANE_TODO_ID.fullmatch(todo_id):
            raise ValueError("governed transition lane settlement todo_id is invalid")
        priority = str(item["priority"])
        if priority not in STEWARD_TEAM_PLAN_PRIORITIES:
            raise ValueError(
                "governed transition lane settlement priority is invalid"
            )
        settlements.append(
            {
                "lane_id": lane_id,
                "agent_id": _plan_text(item["agent_id"], "lane settlement agent_id"),
                "priority": priority,
                "disposition": disposition,
                "todo_id": todo_id,
                "acceptance": _plan_text(
                    item["acceptance"], "lane settlement acceptance"
                ),
            }
        )
    validate_public_safe_value(settlements, path="lane_settlements")
    return settlements


def _monitor_for_key(
    *,
    registry_path: Path,
    goal_id: str,
    monitor_key: str,
) -> dict[str, Any] | None:
    projection = list_goal_todos(
        registry_path=registry_path,
        goal_id=goal_id,
        role="agent",
    )
    matches = [
        item
        for item in projection.get("todos", [])
        if isinstance(item, dict)
        and normalize_todo_capability_binding_ref(item.get("capability_binding_ref"))
        == monitor_key
    ]
    if len(matches) > 1:
        raise ValueError("governed monitor proposal matched multiple Todos")
    if not matches:
        return None
    item = matches[0]
    if item.get("task_class") != TODO_TASK_CLASS_MONITOR:
        raise ValueError("governed monitor proposal matched a non-monitor Todo")
    return item


def _upsert_monitor(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    monitor_key = str(proposal["monitor_key"])
    current = _monitor_for_key(
        registry_path=registry_path,
        goal_id=goal_id,
        monitor_key=monitor_key,
    )
    monitor_metadata = {
        "target_key": str(proposal["target_key"]),
        "cadence": str(proposal["cadence"]),
        "next_due_at": str(proposal["next_due_at"]),
        "expires_at": str(proposal["expires_at"]),
    }
    if current is None:
        result = add_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            role="agent",
            text=str(proposal["text"]),
            status=TODO_STATUS_OPEN,
            task_class=TODO_TASK_CLASS_MONITOR,
            action_kind=str(proposal["action_kind"]),
            capability_binding_ref=monitor_key,
            required_capabilities=[
                str(item) for item in proposal["required_capabilities"]
            ],
            claimed_by=agent_id,
            agent_id=agent_id,
            monitor_metadata=monitor_metadata,
        )
        action = "created" if result.get("added") else "reused"
    else:
        if current.get("status") == TODO_STATUS_DONE:
            raise ValueError("governed monitor proposal cannot reopen a completed Todo")
        if current.get("status") != TODO_STATUS_OPEN:
            raise ValueError("governed monitor proposal requires an open monitor Todo")
        owner = str(current.get("claimed_by") or "")
        if owner and owner != agent_id:
            raise ValueError("governed monitor proposal cannot reassign another Agent")
        result = update_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=str(current["todo_id"]),
            role="agent",
            text=str(proposal["text"]),
            status=TODO_STATUS_OPEN,
            task_class=TODO_TASK_CLASS_MONITOR,
            action_kind=str(proposal["action_kind"]),
            required_capabilities=[
                str(item) for item in proposal["required_capabilities"]
            ],
            claimed_by=agent_id,
            agent_id=agent_id,
            monitor_metadata=monitor_metadata,
        )
        action = "updated" if result.get("changed") else "unchanged"
    return {
        "action": action,
        "todo_id": str(result["todo_id"]),
        "target_key": str(proposal["target_key"]),
    }


def steward_team_plan_basis_agent(plan: Mapping[str, Any]) -> str | None:
    """The Agent an owner-reviewed plan reads its source basis as.

    The source basis is a Goal-level fact, so any of the Goal's Agents reads the
    same one; a ready lane is preferred because that is where the work will live.
    An agent-originated settlement reads it as the settling agent instead.
    """

    lanes = plan.get("lanes") or []
    basis_agent = next(
        (
            str(lane.get("agent_id"))
            for lane in lanes
            if lane.get("staffing") == "ready"
        ),
        str(lanes[0].get("agent_id")) if lanes else "",
    )
    return basis_agent or None


def steward_team_plan_source_basis(
    *,
    goal_id: str,
    goal: Mapping[str, Any],
    registry_path: Path,
    agent_id: str | None,
) -> str | None:
    """Read the canonical source basis one work-graph edit is applied against.

    A Goal whose basis cannot be read omits the field rather than inventing one.
    """

    if not agent_id:
        return None
    try:
        from ...control_plane.goals.shared_goal_alignment import (
            project_shared_goal_alignment,
        )

        alignment = project_shared_goal_alignment(
            goal_id=goal_id,
            agent_id=agent_id,
            project=Path(str(goal.get("repo") or ".")).expanduser(),
            registry_path=Path(registry_path),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None
    basis = (alignment.get("source_basis") or {}).get("source_basis_digest")
    return str(basis) if basis else None


def steward_team_plan_intent_basis(
    *,
    goal_id: str,
    goal: Mapping[str, Any],
    registry_path: Path,
    plan: Mapping[str, Any],
) -> str | None:
    """The canonical source basis a team plan's lanes would be created against.

    A confirmation surface needs this fact *before* the owner confirms, not
    only in the receipt afterwards: it is the intent the plan is reviewed
    against, so a plan confirmed against one basis may not be applied against
    another. The reader is the same one the settlement records, exposed here so
    the preview can bind it instead of re-deriving a second basis.
    """

    return steward_team_plan_source_basis(
        goal_id=goal_id,
        goal=goal,
        registry_path=Path(registry_path),
        agent_id=steward_team_plan_basis_agent(plan),
    )


def _apply_team_plan(
    *, registry_path: Path, goal_id: str, agent_id: str,
    proposal: Mapping[str, Any], expected_state_fingerprint: str | None,
) -> dict[str, Any]:
    """Settle one agent-originated plan against the basis its journal bound.

    The basis was read as this agent before the provider ran and is never
    recomputed here: the typed owner re-reads the current digest and refuses a
    moved or missing basis. A refusal about the plan becomes a typed failure
    result; any other commit error still raises.
    """

    from .team_plan_adapter import TeamPlanCommitError, settle_team_plan
    from ..effect_runtime import EffectRuntimeRejected

    try:
        return settle_team_plan(
            registry_path=registry_path, goal_id=goal_id, agent_id=agent_id,
            proposal=proposal,
            expected_state_fingerprint=expected_state_fingerprint,
        )
    except TeamPlanCommitError as error:
        if error.code in TEAM_PLAN_FAILURE_REASON_CODES:
            return {"action": _FAILED_RECEIPT_ACTION, "todo_id": None,
                    "target_key": None, "reason_code": error.code}
        raise
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from None


def _complete_monitor(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    effect_id: str,
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    current = _monitor_for_key(
        registry_path=registry_path,
        goal_id=goal_id,
        monitor_key=str(proposal["monitor_key"]),
    )
    if current is None:
        raise ValueError("governed monitor completion has no materialized Todo")
    owner = str(current.get("claimed_by") or "")
    if owner and owner != agent_id:
        raise ValueError("governed monitor completion cannot mutate another Agent")
    completion_key = (
        "governed_transition_"
        + hashlib.sha256(f"{effect_id}:{proposal['proposal_id']}".encode()).hexdigest()[
            :32
        ]
    )
    result = complete_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=str(current["todo_id"]),
        role="agent",
        evidence=str(proposal["evidence"]),
        completion_turn_key=completion_key,
        no_followup=True,
        claimed_by=agent_id,
        agent_id=agent_id,
        authority_reason="validated governed external-capability proposal",
    )
    return {
        "action": ("replayed" if result.get("idempotent_replay") else "completed"),
        "todo_id": str(result["todo_id"]),
        "target_key": current.get("target_key"),
    }


def settle_governed_transition_proposals(
    *,
    registry_path: str | Path,
    goal_id: str,
    agent_id: str,
    effect_id: str,
    proposals: Sequence[Mapping[str, Any]],
    existing_receipts: object,
    checkpoint: TransitionCheckpoint,
    phase: GovernedTransitionSettlementPhase,
    team_plan_state_basis: str | None = None,
) -> list[dict[str, Any]]:
    """Apply admitted proposals for one settlement phase and checkpoint receipts.

    ``team_plan_state_basis`` is the state digest the caller bound as this
    agent before any proposal existed (the journal's first write). A team plan
    is settled only against it; without one the typed owner refuses the plan.
    """

    if team_plan_state_basis is not None and (
        not isinstance(team_plan_state_basis, str) or not team_plan_state_basis
    ):
        raise ValueError("governed transition team plan basis is invalid")
    receipts = validate_governed_transition_receipts(existing_receipts)
    by_proposal_id = {str(item["proposal_id"]): item for item in receipts}
    for raw in proposals:
        proposal = _mapping(raw, "governed transition proposal")
        kind = str(proposal.get("kind") or "")
        proposal_phase = _SETTLEMENT_PHASE_BY_PROPOSAL_KIND.get(kind)
        if proposal_phase is None:
            raise ValueError("governed transition proposal kind is unsupported")
        proposal_id = str(proposal.get("proposal_id") or "")
        proposal_digest = _canonical_digest(proposal)
        replay = by_proposal_id.get(proposal_id)
        if replay is not None:
            if (
                replay.get("proposal_digest") != proposal_digest
                or replay.get("kind") != proposal.get("kind")
                or replay.get("monitor_key") != proposal.get("monitor_key")
            ):
                raise ValueError(
                    "governed transition proposal replay does not match its receipt"
                )
            continue
        if proposal_phase is not phase:
            continue
        if kind == "continuous_monitor_upsert":
            result = _upsert_monitor(
                registry_path=Path(registry_path).expanduser(),
                goal_id=goal_id,
                agent_id=agent_id,
                proposal=proposal,
            )
        elif kind == STEWARD_TEAM_PLAN_PREVIEW_KIND:
            result = _apply_team_plan(
                registry_path=Path(registry_path),
                goal_id=goal_id,
                agent_id=agent_id,
                proposal=proposal,
                expected_state_fingerprint=team_plan_state_basis,
            )
        elif kind == "continuous_monitor_complete":
            result = _complete_monitor(
                registry_path=Path(registry_path).expanduser(),
                goal_id=goal_id,
                agent_id=agent_id,
                effect_id=effect_id,
                proposal=proposal,
            )
        else:
            raise RuntimeError("governed transition proposal dispatch is incomplete")
        receipt = {
            "schema_version": GOVERNED_TRANSITION_RECEIPT_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "proposal_digest": proposal_digest,
            "kind": kind,
            "monitor_key": (
                str(proposal["monitor_key"])
                if proposal.get("monitor_key") is not None
                else None
            ),
            "action": str(result["action"]),
            "todo_id": None if result.get("todo_id") is None else str(result["todo_id"]),
            "status": "committed",
            "target_key": result.get("target_key"),
        }
        if result.get("reason_code") is not None:
            # The owner refused the plan before any lane write. The verdict is
            # the settlement's durable answer: a replay reads it back instead
            # of retrying, and it names no work because none exists.
            receipt["status"] = "failed"
            receipt["reason_code"] = str(result["reason_code"])
            validate_public_safe_value(receipt, path="transition_receipt")
            receipts.append(receipt)
            by_proposal_id[proposal_id] = receipt
            checkpoint(receipts)
            continue
        lane_todo_ids = result.get("lane_todo_ids")
        if lane_todo_ids:
            # The apply ensured every ready lane's first Todo; a receipt that
            # named only the first one could not be read as "what exists now".
            receipt["lane_todo_ids"] = [str(item) for item in lane_todo_ids]
        lane_settlements = result.get("lane_settlements")
        if lane_settlements:
            # Retain the relationship, not only the identities: which lane the
            # owner confirmed, what it was meant to end on, and the Todo it
            # became.
            receipt["lane_settlements"] = normalize_lane_settlements(
                [dict(item) for item in lane_settlements]
            )
        gap_count = result.get("gap_count")
        if gap_count:
            # A settlement that staffed some lanes and left others a gap is a
            # partial application, and the reader has to be able to tell
            # without re-deriving the plan.
            receipt["gap_count"] = int(gap_count)
        if result.get("gap_lanes"):
            receipt["gap_lanes"] = result["gap_lanes"]
        lane_failure = result.get("lane_failure")
        if lane_failure:
            # The lanes that exist are named beside the lane that could not be
            # written, so a retry reconciles against real identities instead of
            # re-deriving what the failed attempt managed to commit.
            receipt["lane_failure"] = {
                "lane_id": str(lane_failure["lane_id"]),
                "reason_code": str(lane_failure["reason_code"]),
            }
        if result.get("intent_basis"):
            # The work-graph edit this receipt records is traceable to the
            # canonical basis it was applied against, so a lane Todo can be tied
            # back to the intent revision it was meant to advance.
            receipt["intent_basis"] = str(result["intent_basis"])
        validate_public_safe_value(receipt, path="transition_receipt")
        receipts.append(receipt)
        by_proposal_id[proposal_id] = receipt
        checkpoint(receipts)
    return receipts


STEWARD_TEAM_PLAN_PREVIEW_SCHEMA_VERSION = "steward_team_plan_preview_v0"
STEWARD_TEAM_PLAN_LANE_LIMIT = 8
STEWARD_TEAM_PLAN_PRIORITIES = tuple(COORDINATION_STATE_CONTRACT["todo_priority"]["values"])
_GOAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
# The reasons a plan may declare for a lane it cannot staff itself.
STEWARD_TEAM_PLAN_GAP_REASONS = (
    "agent_not_registered",
    "capability_not_granted",
    "audience_not_authorized",
)
# The reasons this host reports for a lane *it* cannot staff. They are the
# host's own verdict about the same lane fact, so they stay a separate
# vocabulary: a plan may not claim one of these to describe its own lane, and a
# reader can tell an owner-declared gap from a staffability verdict Core made.
STEWARD_TEAM_PLAN_UNSUPPORTED_ACTION_KIND = "action_kind_not_supported"
STEWARD_TEAM_PLAN_HOST_GAP_REASONS = (STEWARD_TEAM_PLAN_UNSUPPORTED_ACTION_KIND,)


def _plan_text(value: object, label: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{label} must be a non-empty string")
    if len(text) > 600:
        raise ValueError(f"{label} exceeds the public-safe preview length")
    validate_public_safe_value({"value": text}, path=label)
    return text


def validate_steward_team_plan_preview(
    payload: object, *, registered_agent_ids: Sequence[str],
    supported_action_kinds: Sequence[str],
) -> dict[str, Any]:
    """Public-safety adapter; the typed work-items owner admits the preview."""
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    validate_public_safe_value(payload, path="steward_team_plan_preview")
    try:
        result = effect_runtime_result("work_items.team_plan.preview", {
            "plan": payload, "registered_agents": list(registered_agent_ids),
            "supported_action_kinds": list(supported_action_kinds),
        })
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from None
    validate_public_safe_value(result, path="steward_team_plan_preview")
    return dict(result)
