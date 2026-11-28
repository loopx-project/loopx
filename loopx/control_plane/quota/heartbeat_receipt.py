from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path

from ...file_lock import exclusive_cross_runtime_file_lock
from ...rollout_event_log import (
    _append_rollout_event_line,
    build_rollout_event,
    iter_rollout_events,
    load_rollout_events,
    rollout_event_log_path,
)
from ..todos.contract import normalize_todo_id, normalize_todo_replan_obligation_id
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .effective_action import EffectiveAction
from .effect_program import SettlementBindingKind, SettlementIdentity
from .error_codes import HeartbeatReceiptIdentityConflictError
from .semantic_replan_guard_merge import (
    SEMANTIC_REPLAN_REVIEW_FIELDS,
    append_repaired_identity_upgrade_receipt,
    compute_repaired_identity_upgrade_replan_guard_details,
    merge_semantic_replan_guard_details,
    repair_orphaned_identity_upgrade_heartbeat_receipt as repair_orphaned_identity_upgrade_heartbeat_receipt,  # noqa: F401
    repair_orphaned_identity_upgrade_heartbeat_receipts_for_agent as repair_orphaned_identity_upgrade_heartbeat_receipts_for_agent,  # noqa: F401
    strip_semantic_replan_companions,
)
from .settlement_workspace_causality import (
    delivery_workspace_causality_from_event_details,
)
from .spend_sources import TURN_SCOPED_SLOT_SPEND_SOURCES

HEARTBEAT_RECEIPT_SCHEMA_VERSION = "heartbeat_quota_receipt_v0"


def _agent_heartbeat_receipt_events(
    events: list[dict[str, object]],
    *,
    goal_id: str,
    agent_id: str,
    goal_ref: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    from ..goals.source_session_registry_state import exact_goal_ref

    expected_goal_ref = (
        exact_goal_ref(
            str(goal_ref.get("goal_id") or ""),
            str(goal_ref.get("goal_instance_id") or ""),
        )
        if goal_ref is not None
        else None
    )
    if expected_goal_ref is not None and expected_goal_ref["goal_id"] != goal_id:
        raise ValueError("heartbeat receipt GoalRef does not match goal_id")
    return [
        event
        for event in events
        if event.get("event_kind") == "quota_should_run"
        and str(event.get("goal_id") or "") == goal_id
        and str(event.get("agent_id") or "") == agent_id
        and (
            event.get("goal_ref") == expected_goal_ref
            if expected_goal_ref is not None
            else "goal_ref" not in event
        )
    ]


def _heartbeat_receipt_events(
    events: list[dict[str, object]],
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    return [
        event
        for event in _agent_heartbeat_receipt_events(
            events, goal_id=goal_id, agent_id=agent_id, goal_ref=goal_ref,
        )
        if str(event.get("run_id") or "") == turn_instance_id
    ]


def heartbeat_receipt_request_subject(event: Mapping[str, object] | None) -> dict[str, object] | None:
    """Transport the original request's path-free identity from scalar event details."""
    details = (event or {}).get("details")
    if not isinstance(details, Mapping) or "request_id" not in details:
        return None
    return {key: details.get(key) for key in (
        "request_id", "request_revision", "request_digest", "execution_scope_sha256")}


@lru_cache(maxsize=256)
def _receipt_selection(
    facts_json: str,
) -> tuple[int | None, tuple[SettlementBindingKind, str, str] | None]:
    """Cache pure binding facts, never a file read, lease, clock or admission."""
    try:
        result = effect_runtime_result("quota.heartbeat_receipt.select", json.loads(facts_json))
    except EffectRuntimeRejected as exc:
        if exc.diagnostic_code == "heartbeat_receipt_identity_conflict":
            raise HeartbeatReceiptIdentityConflictError(str(exc)) from None
        raise ValueError(str(exc)) from None
    index, binding = result.get("index"), result.get("binding")
    if index is not None and (type(index) is not int or index < 0):
        raise TypeError("TypeScript heartbeat receipt selection index mismatch")
    if binding is None:
        return index, None
    if not isinstance(binding, Mapping):
        raise TypeError("TypeScript heartbeat receipt binding shape mismatch")
    return index, (
        SettlementBindingKind(str(binding["binding_kind"])),
        str(binding["binding_id"]), str(binding["effect_id"]),
    )


def _select_receipt_facts(
    events: Sequence[Mapping[str, object]],
) -> tuple[int | None, tuple[SettlementBindingKind, str, str] | None]:
    # Keep unknown details in the reader. Only the shared identity owner's raw
    # fields enter this pure projection/cache; no receipt or source is cached.
    facts = []
    for event in events:
        details = event.get("details")
        facts.append({"run_id": event.get("run_id"), "details": {
            key: details[key] for key in (
                "todo_id", "replan_obligation_id", "settlement_effect_id",
                "request_id", "request_revision", "request_digest", "execution_scope_sha256",
            ) if isinstance(details, Mapping) and key in details
        }})
    first = events[0]
    return _receipt_selection(json.dumps({
        "goal_id": str(first.get("goal_id") or "").strip(),
        "agent_id": str(first.get("agent_id") or "").strip(),
        "events": facts,
    }, sort_keys=True))


def _receipt_settlement_identity(
    event: Mapping[str, object],
) -> tuple[SettlementBindingKind, str, str] | None:
    return _select_receipt_facts([event])[1]


def heartbeat_receipt_settlement_todo_id(
    event: Mapping[str, object],
) -> str | None:
    """Return the Todo already bound to a committed heartbeat receipt."""

    identity = _receipt_settlement_identity(event)
    if identity is None or identity[0] is not SettlementBindingKind.TODO:
        return None
    return identity[1]


def heartbeat_receipt_settlement_replan_obligation_id(
    event: Mapping[str, object],
) -> str | None:
    """Return the autonomous replan bound to a committed heartbeat receipt."""

    identity = _receipt_settlement_identity(event)
    if identity is None or identity[0] is not SettlementBindingKind.AUTONOMOUS_REPLAN:
        return None
    return identity[1]


def heartbeat_receipt_semantic_replan_obligation_id(
    event: Mapping[str, object] | None,
) -> str | None:
    """Return the semantic replan target selected by the original guard.

    This is deliberately separate from the settlement identity: a replan Turn
    may settle through a concrete Todo while that same guard requires a typed
    semantic delta for an autonomous replan obligation.
    """

    if not isinstance(event, Mapping):
        return None
    details = event.get("details")
    if not isinstance(details, Mapping):
        return None
    return normalize_todo_replan_obligation_id(
        details.get("semantic_replan_obligation_id")
    )


def heartbeat_receipt_pending_action_todo_id(
    event: Mapping[str, object] | None,
) -> str | None:
    """Return an explicit Todo choice retained before settlement binding."""

    if not isinstance(event, Mapping):
        return None
    details = event.get("details")
    if not isinstance(details, Mapping):
        return None
    return normalize_todo_id(details.get("pending_action_selection_todo_id"))


def _effective_heartbeat_receipt(
    events: list[dict[str, object]],
) -> tuple[dict[str, object] | None, tuple[SettlementBindingKind, str, str] | None]:
    if not events:
        return None, None
    index, binding = _select_receipt_facts(events)
    if index is None or index >= len(events):
        raise TypeError("TypeScript heartbeat receipt selection did not address the supplied events")
    return events[index], binding


def find_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    events = list(iter_rollout_events(
        rollout_event_log_path(runtime_root, goal_id), committed_only=True,
    ))
    event, _ = _effective_heartbeat_receipt(
        _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
    )
    return event


def ensure_turn_heartbeat_settlement_receipt(
    runtime_root: Path,
    identity: SettlementIdentity,
    *,
    semantic_replan_guard_scoped: bool,
    semantic_replan_obligation_id: str | None,
    semantic_replan_capability_guard: Mapping[str, object] | None = None,
    quota_spend_source: str | None = None,
    task_lease_proof: Mapping[str, object] | None = None,
    task_lease_epoch: int | None = None,
    prepared_lease_required: bool = False,
    goal_ref: Mapping[str, object] | None = None,
    before_bind: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Idempotently bind a Turn-created quota guard to its settlement identity.

    Current Turn envelopes always carry ``replan_action_packet`` even when no
    obligation was selected.  Persist that explicit empty selection so an
    obligation opened while the host is running cannot retroactively reject
    the admitted Turn.  Old envelopes without the field stay legacy-unscoped.
    """

    normalized_semantic_replan_obligation_id = (
        normalize_todo_replan_obligation_id(semantic_replan_obligation_id)
    )
    if (
        semantic_replan_obligation_id is not None
        and normalized_semantic_replan_obligation_id is None
    ):
        raise ValueError("Turn semantic replan obligation id is malformed")
    if (
        semantic_replan_guard_scoped
        and semantic_replan_capability_guard is not None
        and normalized_semantic_replan_obligation_id is None
    ):
        raise ValueError(
            "Turn semantic replan capability guard requires a selected obligation"
        )

    if quota_spend_source is not None and quota_spend_source not in TURN_SCOPED_SLOT_SPEND_SOURCES:
        raise ValueError("Turn quota spend source must be heartbeat or visible-goal")
    log_path = rollout_event_log_path(runtime_root, identity.goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_cross_runtime_file_lock(
        log_path, operation="bind_heartbeat_settlement_receipt"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=identity.goal_id,
            agent_id=identity.agent_id,
            turn_instance_id=identity.turn_instance_id,
            goal_ref=goal_ref,
        )
        effective, observed = _effective_heartbeat_receipt(matching)
        expected = (
            identity.binding_kind,
            identity.binding_id,
            identity.effect_id,
        )
        if observed is not None and observed != expected:
            raise HeartbeatReceiptIdentityConflictError(
                "Turn heartbeat receipt belongs to another settlement identity"
            )
        previous_details = effective.get("details") if effective is not None else None
        previous_source = (
            previous_details.get("quota_spend_source")
            if observed is not None and isinstance(previous_details, Mapping) else None
        )
        if previous_source is not None and quota_spend_source not in (None, previous_source):
            raise HeartbeatReceiptIdentityConflictError(
                "Turn heartbeat receipt belongs to another quota spend source"
            )
        if before_bind is not None:
            # The existing cross-runtime binding lock serializes the competing
            # identity before transport-specific start admission. Its owner may
            # refuse admission without writing a receipt or claiming work.
            before_bind()
        if task_lease_proof is not None and identity.todo_id is not None:
            from .execution_preparation import prepared_lease_receipt_fields

            rebound_lease_fields = prepared_lease_receipt_fields(
                task_lease_proof, lease_epoch=task_lease_epoch,
            )
        else:
            rebound_lease_fields = None
        preserved_details: dict[str, object] = {}
        if effective is not None:
            if observed is not None:
                effective_details = effective.get("details")
                effective_details = (
                    effective_details
                    if isinstance(effective_details, Mapping)
                    else {}
                )
                preserved_details = dict(effective_details)
                lease_unchanged = rebound_lease_fields is None or (
                    all(effective_details.get(k) == v for k, v in rebound_lease_fields.items())
                    and (task_lease_epoch is not None or "prepared_lease_epoch" not in effective_details)
                )
                if not semantic_replan_guard_scoped:
                    if lease_unchanged:
                        return effective
                elif "semantic_replan_obligation_id" in effective_details:
                    raw_existing_guard = effective_details.get(
                        "semantic_replan_obligation_id"
                    )
                    existing_guard = normalize_todo_replan_obligation_id(
                        raw_existing_guard
                    )
                    if (
                        str(raw_existing_guard or "").strip()
                        and existing_guard is None
                    ):
                        raise HeartbeatReceiptIdentityConflictError(
                            "Turn heartbeat receipt has a malformed semantic replan guard"
                        )
                    if existing_guard != normalized_semantic_replan_obligation_id:
                        raise HeartbeatReceiptIdentityConflictError(
                            "Turn heartbeat receipt belongs to another semantic replan guard"
                        )
                    if semantic_replan_capability_guard is not None and any(
                        effective_details.get(source) != semantic_replan_capability_guard[field]
                        for source, field in (
                            ("semantic_replan_capability_id", "capability_id"),
                            ("semantic_replan_gap_id", "gap_id"),
                            ("semantic_replan_frontier_revision", "frontier_revision"),
                        )
                    ):
                        raise HeartbeatReceiptIdentityConflictError(
                            "Turn heartbeat receipt belongs to another capability guard"
                        )
                    if lease_unchanged:
                        return effective

        details = {
            **preserved_details,
            "turn_instance_id": identity.turn_instance_id,
            "todo_id": str(identity.todo_id or ""),
            "replan_obligation_id": str(identity.replan_obligation_id or ""),
            **(dict(identity.request_subject) if identity.request_subject is not None else {}),
            "settlement_effect_id": identity.effect_id,
            "stall_observation": "not_applicable",
            "source": "loopx_turn_run_once",
        }
        if identity.todo_id is not None and (prepared_lease_required or task_lease_proof is not None):
            details["prepared_lease_required"] = True
        if rebound_lease_fields is not None:
            details.update(rebound_lease_fields)
            if task_lease_epoch is None:
                details.pop("prepared_lease_epoch", None)
        if semantic_replan_guard_scoped:
            if semantic_replan_capability_guard is not None and any(
                field in details for field in SEMANTIC_REPLAN_REVIEW_FIELDS
            ):
                raise HeartbeatReceiptIdentityConflictError(
                    "Turn heartbeat receipt belongs to a review guard"
                )
            details["semantic_replan_obligation_id"] = (
                normalized_semantic_replan_obligation_id or ""
            )
            if semantic_replan_capability_guard is not None:
                details["semantic_replan_capability_id"] = semantic_replan_capability_guard["capability_id"]
                details["semantic_replan_gap_id"] = semantic_replan_capability_guard["gap_id"]
                details["semantic_replan_frontier_revision"] = semantic_replan_capability_guard["frontier_revision"]
            elif normalized_semantic_replan_obligation_id is None:
                details = strip_semantic_replan_companions(details)
        if (source := previous_source or quota_spend_source) is not None:
            details["quota_spend_source"] = source
        source_event_id = (
            str(effective.get("event_id") or "").strip()
            if effective is not None
            else ""
        )
        receipt = build_rollout_event(
            goal_id=identity.goal_id,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
            agent_id=identity.agent_id,
            todo_id=identity.todo_id,
            run_id=identity.turn_instance_id,
            status="turn_run_once",
            summary=f"Turn settlement guard prepared for turn={identity.turn_instance_id}",
            source_event_id=source_event_id or None,
            caused_by=source_event_id or None,
            details=details,
        )
        _append_rollout_event_line(log_path, receipt)
        return receipt


def retain_pending_heartbeat_action_selection(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str,
    reason: str,
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], bool]:
    """Append an identity-less revision retaining one explicit Todo choice.

    The choice is not settlement authority.  It only fences a later no-argument
    reentry from silently replacing the agent's explicit selection with a new
    recommendation.  A later explicit selection may replace it until the Turn
    acquires its single settlement identity.
    """

    normalized_todo_id = normalize_todo_id(todo_id)
    if normalized_todo_id is None:
        raise HeartbeatReceiptIdentityConflictError(
            "pending heartbeat action selection requires a legal Todo id"
        )
    normalized_reason = str(reason or "current_delivery_gate").strip()
    log_path = rollout_event_log_path(runtime_root, goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_cross_runtime_file_lock(
        log_path, operation="retain_heartbeat_action_selection"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
        effective, binding = _effective_heartbeat_receipt(matching)
        if effective is None:
            raise ValueError(
                "identity-less heartbeat receipt is missing; rerun the original guard"
            )
        if binding is not None:
            raise HeartbeatReceiptIdentityConflictError(
                "a settled heartbeat Turn cannot retain a different pending selection"
            )
        details_value = effective.get("details")
        details = (
            dict(details_value) if isinstance(details_value, Mapping) else {}
        )
        if (
            normalize_todo_id(details.get("pending_action_selection_todo_id"))
            == normalized_todo_id
            and str(details.get("pending_action_selection_reason") or "").strip()
            == normalized_reason
        ):
            return effective, False
        details.update(
            {
                "pending_action_selection_todo_id": normalized_todo_id,
                "pending_action_selection_state": "deferred",
                "pending_action_selection_reason": normalized_reason,
                "settlement_effect_id": "",
                "todo_id": "",
                "replan_obligation_id": "",
            }
        )
        source_event_id = str(effective.get("event_id") or "").strip() or None
        retained = build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
            agent_id=agent_id,
            run_id=turn_instance_id,
            status="action_selection_deferred",
            summary=(
                "heartbeat explicit action selection retained without settlement "
                f"binding for turn={turn_instance_id}"
            ),
            source_event_id=source_event_id,
            caused_by=source_event_id,
            details=details,
        )
        _append_rollout_event_line(log_path, retained)
        return retained, True


def upgrade_identityless_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    settlement_effect_id: str,
    status: str,
    summary: str,
    details: Mapping[str, object],
    goal_ref: Mapping[str, object] | None = None,
    required_semantic_replan_obligation_id: str | None = None,
) -> tuple[dict[str, object], bool]:
    """Append one settlement-bound receipt after an identity-less same-turn guard.

    The correction is append-only and serialized with the rollout log lock. A
    matching correction replays, a legacy Todo-only receipt gains its derived
    effect id, and effect-only or conflicting identities fail closed.
    """

    normalized_todo_id = str(todo_id or "").strip()
    normalized_replan_obligation_id = normalize_todo_replan_obligation_id(
        replan_obligation_id
    )
    normalized_effect_id = str(settlement_effect_id).strip()
    normalized_required_semantic_replan_obligation_id = (
        normalize_todo_replan_obligation_id(
            required_semantic_replan_obligation_id
        )
    )
    if (
        required_semantic_replan_obligation_id is not None
        and normalized_required_semantic_replan_obligation_id is None
    ):
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires a valid semantic replan obligation"
        )
    if bool(normalized_todo_id) == bool(normalized_replan_obligation_id):
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires exactly one deterministic "
            "settlement binding"
        )
    try:
        expected_identity_value = SettlementIdentity(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=normalized_todo_id or None,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=normalized_replan_obligation_id,
        )
    except ValueError as exc:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires exactly one deterministic "
            "settlement binding"
        ) from exc
    expected_effect_id = expected_identity_value.effect_id
    if normalized_effect_id != expected_effect_id:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires the deterministic settlement identity"
        )

    log_path = rollout_event_log_path(runtime_root, goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_cross_runtime_file_lock(
        log_path, operation="upgrade_heartbeat_receipt_identity"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
        effective, existing_identity = _effective_heartbeat_receipt(matching)
        if effective is None:
            raise ValueError(
                "identity-less heartbeat receipt is missing; rerun the original guard"
            )
        effective_details_value = effective.get("details")
        effective_details: Mapping[str, object] = (
            effective_details_value
            if isinstance(effective_details_value, Mapping)
            else {}
        )
        if normalized_required_semantic_replan_obligation_id is not None:
            selected_semantic_replan_obligation_id = (
                heartbeat_receipt_semantic_replan_obligation_id(effective)
            )
            if (
                selected_semantic_replan_obligation_id
                != normalized_required_semantic_replan_obligation_id
            ):
                raise HeartbeatReceiptIdentityConflictError(
                    "heartbeat receipt semantic replan guard conflicts with the "
                    "requested successor obligation"
                )
            if not (
                effective_details.get("interaction_mode") == "autonomous_replan"
                and effective_details.get("ok") is True
                and effective_details.get("should_run") is True
                and effective_details.get("must_attempt_work") is True
                and effective_details.get("delivery_allowed") is False
                and effective_details.get("quiet_noop_allowed") is False
            ):
                raise HeartbeatReceiptIdentityConflictError(
                    "heartbeat receipt did not admit an autonomous replan successor"
                )
        expected_identity = (
            expected_identity_value.binding_kind,
            expected_identity_value.binding_id,
            normalized_effect_id,
        )
        if existing_identity is not None:
            if existing_identity != expected_identity:
                if (
                    existing_identity[0] is SettlementBindingKind.TODO
                    and expected_identity[0] is SettlementBindingKind.TODO
                ):
                    conflict_target = "current selected Todo"
                elif (
                    existing_identity[0] is SettlementBindingKind.AUTONOMOUS_REPLAN
                    and expected_identity[0] is SettlementBindingKind.AUTONOMOUS_REPLAN
                ):
                    conflict_target = "current autonomous replan obligation"
                else:
                    conflict_target = "current settlement binding"
                raise HeartbeatReceiptIdentityConflictError(
                    "heartbeat receipt settlement identity conflicts with the "
                    f"{conflict_target}"
                )
            existing_details_value = effective.get("details")
            existing_details = (
                existing_details_value
                if isinstance(existing_details_value, Mapping)
                else {}
            )
            if str(existing_details.get("settlement_effect_id") or "").strip():
                repaired_details = (
                    compute_repaired_identity_upgrade_replan_guard_details(
                        effective, matching
                    )
                )
                if repaired_details is not None:
                    repaired = append_repaired_identity_upgrade_receipt(
                        log_path,
                        goal_id=goal_id,
                        agent_id=agent_id,
                        turn_instance_id=turn_instance_id,
                        effective=effective,
                        repaired_details=repaired_details,
                        goal_ref=goal_ref,
                    )
                    return repaired, True
                return effective, False

        # The effective receipt was read while holding the rollout lock. Keep
        # non-conflicting fields appended by a concurrent same-Turn revision,
        # while stripping orphaned semantic replan companion metadata whenever
        # the guard selection clears, rotates, or changes kind.
        corrected_details = merge_semantic_replan_guard_details(
            effective_details,
            details,
        )
        corrected_details.update(
            {
                "turn_instance_id": turn_instance_id,
                "todo_id": normalized_todo_id,
                "replan_obligation_id": normalized_replan_obligation_id or "",
                "settlement_effect_id": normalized_effect_id,
                "settlement_receipt_revision": "identity_upgrade",
            }
        )
        source_event_id = str(effective.get("event_id") or "").strip() or None
        corrected = build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
            agent_id=agent_id,
            todo_id=normalized_todo_id or None,
            run_id=turn_instance_id,
            status=status,
            summary=summary,
            source_event_id=source_event_id,
            caused_by=source_event_id,
            details=corrected_details,
        )
        _append_rollout_event_line(log_path, corrected)
        return corrected, True


def bind_replan_successor_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    replan_obligation_id: str,
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], bool]:
    """Bind an identity-less admitted replan when its successor is selected.

    Multi-candidate planning guards deliberately avoid binding a recommended
    Todo.  Creating a concrete successor is nevertheless an explicit semantic
    replan choice.  Upgrade that exact Turn to the already selected obligation
    before the Todo mutation so later writeback and spend reuse one causal
    identity.  The upgrade rechecks the effective guard while holding the
    rollout lock; stale or non-admitted semantic targets fail closed.
    """

    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=None,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
    )
    return upgrade_identityless_heartbeat_receipt(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
        settlement_effect_id=identity.effect_id,
        status="autonomous_replan_required",
        summary=(
            "heartbeat quota receipt bound by explicit replan successor selection "
            f"for turn={turn_instance_id}"
        ),
        details={},
        goal_ref=goal_ref,
        required_semantic_replan_obligation_id=replan_obligation_id,
    )


def requalify_bound_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str | None,
    replan_obligation_id: str | None,
    status: str,
    details: dict[str, object],
    registry_path: Path | None,
    goal_ref: Mapping[str, object] | None,
) -> tuple[dict[str, object], bool]:
    """Transport current guard facts; TS owns same-binding work qualification."""
    from .settlement import read_heartbeat_settlement

    log_path = rollout_event_log_path(runtime_root, goal_id)
    with ExitStack() as locks:
        locks.enter_context(exclusive_cross_runtime_file_lock(log_path, operation="requalify_heartbeat_work"))
        original, _ = _effective_heartbeat_receipt(_heartbeat_receipt_events(
            load_rollout_events(log_path), goal_id=goal_id, agent_id=agent_id,
            turn_instance_id=turn_instance_id, goal_ref=goal_ref,
        ))
        original_details_value = original.get("details") if original else None
        original_details = original_details_value if isinstance(original_details_value, Mapping) else {}
        # Snapshot only the changed-generation path. The TS receipt owner alone
        # decides eligibility. A nonblocking maintenance fence avoids reversing
        # the ordinary maintenance -> receipt lock order while keeping the source
        # unchanged through the append below.
        guard_details = dict(details)
        guard_details.pop("lease_takeover_source", None)
        if (todo_id and original_details.get("prepared_lease_key_sha256")
                and details.get("prepared_lease_key_sha256")
                and (original_details.get("prepared_lease_key_sha256") != details.get("prepared_lease_key_sha256")
                     or original_details.get("prepared_lease_epoch") != details.get("prepared_lease_epoch"))):
            from ..coordination.shadow_management import (
                require_shadow_primary_write_allowed, shadow_maintenance_lock_target,
            )
            from ..runtime.time import now_local_iso
            from .execution_preparation import _read_execution_snapshot

            locks.enter_context(exclusive_cross_runtime_file_lock(
                shadow_maintenance_lock_target(runtime_root, goal_id), timeout_seconds=0,
                operation="requalify_heartbeat_work",
            ))
            require_shadow_primary_write_allowed(runtime_root, goal_id)
            snapshot = _read_execution_snapshot(runtime_root, goal_id, todo_id, include_takeover_basis=True)
            if snapshot is not None:
                guard_details["lease_takeover_source"] = {**snapshot, "observed_at": now_local_iso(timespec="microseconds")}
        readback = read_heartbeat_settlement(
            runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            turn_instance_id=turn_instance_id, replan_obligation_id=replan_obligation_id,
            registry_path=registry_path, goal_ref=goal_ref, heartbeat_reentry_guard=guard_details,
        )
        if original is None or readback is None or readback.identity.value is None:
            raise ValueError("work requalification requires the original admitted settlement identity")
        if readback.heartbeat_receipt is None or readback.heartbeat_receipt.get("event_id") != original.get("event_id"):
            raise ValueError("work requalification receipt readback changed")
        qualification = readback.heartbeat_reentry_qualification
        if not isinstance(qualification, Mapping) or not isinstance(qualification.get("append"), bool):
            raise RuntimeError("TypeScript heartbeat work qualification shape mismatch")
        if not qualification["append"]:
            return original, False
        qualified_details = qualification.get("details")
        if not isinstance(qualified_details, Mapping):
            raise RuntimeError("TypeScript heartbeat work qualification details missing")
        event_id = str(original.get("event_id") or "")
        receipt_revision = str(
            qualified_details.get("settlement_receipt_revision") or ""
        )
        summary = {
            "review_guard_upgrade": "same-Turn review guard upgraded without rebinding",
            "lease_readiness": "same-Turn lease readiness recorded without readmission",
            "lease_takeover": "current holder resumed the original Turn; validate saved results against current requirements",
        }.get(receipt_revision, "same-Turn work admission requalified without rebinding")
        corrected = build_rollout_event(
            goal_id=goal_id, goal_ref=goal_ref, event_kind="quota_should_run",
            agent_id=agent_id, todo_id=todo_id, run_id=turn_instance_id,
            status=str(original.get("status") or status) if receipt_revision in {"lease_readiness", "lease_takeover"} else status,
            summary=summary,
            source_event_id=event_id, caused_by=event_id, details=dict(qualified_details),
        )
        _append_rollout_event_line(log_path, corrected)
        return corrected, True


def heartbeat_receipt_view(
    event: Mapping[str, object],
    *,
    turn_instance_id: str,
    status: str,
) -> dict[str, object]:
    details_value = event.get("details")
    details: Mapping[str, object] = (
        details_value if isinstance(details_value, Mapping) else {}
    )
    receipt: dict[str, object] = {
        "schema_version": HEARTBEAT_RECEIPT_SCHEMA_VERSION,
        "turn_instance_id": turn_instance_id,
        "status": status,
        "stall_observation": str(details.get("stall_observation") or "not_applicable"),
        "event_id": event.get("event_id"),
        "recorded_at": event.get("recorded_at"),
    }
    if details.get("closeout_required") is True:
        receipt["closeout_required"] = True
    if details.get("takeover_original_event_id"):
        receipt["lease_takeover"] = {
            "original_receipt_event_id": details["takeover_original_event_id"],
            "original_lease_epoch": details.get("takeover_original_epoch"),
            "current_lease_epoch": details.get("prepared_lease_epoch"),
            "evidence_policy": (
                "Current ownership resumed the original Turn, not acceptance of old results. "
                "Read current requirements, dependencies and artifact revisions; reuse only evidence "
                "that remains valid, run affected validation, then settle this original identity once."
            ),
        }
    todo_id = str(details.get("todo_id") or "").strip()
    replan_obligation_id = normalize_todo_replan_obligation_id(
        details.get("replan_obligation_id")
    )
    effect_id = str(details.get("settlement_effect_id") or "").strip()
    request_subject = heartbeat_receipt_request_subject(event)
    if (todo_id or replan_obligation_id or request_subject) and effect_id:
        identity = SettlementIdentity(
            goal_id=str(event.get("goal_id") or ""),
            agent_id=str(event.get("agent_id") or ""),
            todo_id=todo_id or None,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=replan_obligation_id,
            request_subject=request_subject,
        )
        receipt["settlement_identity"] = identity.as_dict()
        workspace_causality = delivery_workspace_causality_from_event_details(
            details,
            todo_id=todo_id or None,
        )
        if workspace_causality:
            receipt["delivery_workspace_causality"] = workspace_causality
    else:
        # The documented wake order runs the guard before a work item is chosen,
        # so a turn-scoped receipt can legitimately commit with no settlement
        # binding. Saying so here is what keeps that receipt from reading as a
        # finished guard: the caller otherwise discovers the debt only when the
        # closeout cannot settle the turn and the work stays unaccounted.
        receipt["settlement_binding_owed"] = True
        if details.get("selection_required") is True:
            receipt["selection_required"] = True
    semantic_replan_obligation_id = (
        heartbeat_receipt_semantic_replan_obligation_id(event)
    )
    if semantic_replan_obligation_id:
        receipt["semantic_replan_obligation_id"] = (
            semantic_replan_obligation_id
        )
    if details.get("semantic_replan_capability_id"):
        receipt["semantic_replan_capability_guard"] = {
            "schema_version": "semantic_replan_capability_guard_v0",
            "capability_id": details["semantic_replan_capability_id"],
            "gap_id": details.get("semantic_replan_gap_id"),
            "frontier_revision": details.get("semantic_replan_frontier_revision"),
        }
    pending_action_todo_id = heartbeat_receipt_pending_action_todo_id(event)
    if pending_action_todo_id:
        receipt["pending_action_selection"] = {
            "todo_id": pending_action_todo_id,
            "state": str(
                details.get("pending_action_selection_state") or "deferred"
            ),
            "reason": str(
                details.get("pending_action_selection_reason")
                or "current_delivery_gate"
            ),
            "settlement_bound": (
                details.get("pending_action_selection_settlement_bound") is True
            ),
        }
    return receipt


def attach_uncommitted_heartbeat_receipt(
    payload: dict[str, object],
    *,
    turn_instance_id: str,
) -> None:
    """Expose an accurate non-durable receipt for an incomplete preflight."""

    payload["heartbeat_receipt"] = {
        "schema_version": HEARTBEAT_RECEIPT_SCHEMA_VERSION,
        "turn_instance_id": turn_instance_id,
        "status": "not_committed",
        "stall_observation": "not_evaluated",
        "reason_code": str(
            payload.get("error_code") or "quota_preflight_incomplete"
        ),
    }


def fail_heartbeat_receipt(
    payload: dict[str, object],
    *,
    turn_instance_id: str,
    stall_observation: str,
    reason: str,
) -> None:
    payload.update(
        {
            "ok": False,
            "decision": "skip",
            "should_run": False,
            "effective_action": EffectiveAction.HEARTBEAT_RECEIPT_WRITE_FAILED.value,
            "state": "blocked_health",
            "waiting_on": "codex",
            "reason": reason,
            "recommended_action": (
                "retry quota should-run with the same --turn-instance-id after "
                "repairing heartbeat receipt writeback"
            ),
            "heartbeat_receipt": {
                "schema_version": HEARTBEAT_RECEIPT_SCHEMA_VERSION,
                "turn_instance_id": turn_instance_id,
                "status": "write_failed",
                "stall_observation": stall_observation,
            },
        }
    )
