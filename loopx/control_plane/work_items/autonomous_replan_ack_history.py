"""GoalRef ownership filtering and receipt-bound rollout history order helpers."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import TypeVar

from ..goals.goal_ref_validation import exact_goal_ref


AckMapping = Mapping[str, object]
AckObject = dict[str, object]
_RunT = TypeVar("_RunT", bound=Mapping[str, object])
_MutableRecordT = TypeVar("_MutableRecordT", bound=MutableMapping[str, object])


def _binding_text(value: object) -> str:
    return str(value or "").strip()


def _event_details(event: AckMapping) -> AckMapping:
    details = event.get("details")
    return details if isinstance(details, Mapping) else {}


def _parsed_exact_goal_ref(value: object) -> dict[str, str] | None:
    """Parse one exact GoalRef, keeping the native string-only rule."""
    if not isinstance(value, Mapping):
        return None
    goal_id = value.get("goal_id")
    instance_id = value.get("goal_instance_id")
    if not isinstance(goal_id, str) or not isinstance(instance_id, str):
        return None
    try:
        return exact_goal_ref(goal_id, instance_id)
    except ValueError:
        return None


def _owner_goal_ref(
    goal_ref: AckMapping | None, *, goal_id: str,
) -> dict[str, str] | None:
    """Normalize the quota owner. None is the alias owner."""
    if goal_ref is None:
        return None
    owner = _parsed_exact_goal_ref(goal_ref)
    if owner is None or owner["goal_id"] != _binding_text(goal_id):
        raise ValueError("historical ACK GoalRef does not match goal_id")
    return owner


def _record_owned_by_goal_ref(
    record: AckMapping, owner: dict[str, str] | None,
) -> bool:
    """Match quotaOwnerOwnsProjection for one rollout or run-index row.

    An alias owner keeps only rows with no goal_ref. An exact owner keeps
    only rows whose goal_ref is that exact Goal instance. Non-string ids are
    not owned, matching the strict GoalRef parser.
    """
    if owner is None:
        return "goal_ref" not in record
    if "goal_ref" not in record:
        return False
    return _parsed_exact_goal_ref(record.get("goal_ref")) == owner


def drop_unowned_recorded_replan_acks(
    runs: Sequence[_RunT] | None, *, goal_id: str,
    goal_ref: AckMapping | None,
) -> tuple[list[_RunT], bool]:
    """Drop recorded replan ACKs that belong to another Goal instance.

    Other run rows stay. Alias owners keep ACKs with no goal_ref; an exact
    owner keeps only its own goal_ref.
    """
    owner = _owner_goal_ref(goal_ref, goal_id=goal_id)
    kept: list[_RunT] = []
    changed = False
    for run in runs or ():
        if not isinstance(run, Mapping):
            continue
        ack = run.get("autonomous_replan_ack")
        if (
            isinstance(ack, Mapping)
            and ack.get("recorded") is True
            and not _record_owned_by_goal_ref(run, owner)
        ):
            changed = True
            continue
        kept.append(run)
    return kept, changed


def _owned_records(
    records: Sequence[_RunT] | None, owner: dict[str, str] | None,
) -> list[_RunT]:
    return [
        record for record in records or ()
        if isinstance(record, Mapping) and _record_owned_by_goal_ref(record, owner)
    ]


def _admission_event_index(
    events: Sequence[AckMapping], *, goal_id: str, agent_id: str,
    turn_instance_id: str, recorded_at: str,
    owner_goal_ref: dict[str, str] | None,
    event_id: str | None = None,
) -> int | None:
    """Log position of the Turn's original bound admission.

    The settlement readback names that event after GoalRef filtering. Matching
    the id keeps a retired Goal instance that reused this Turn id out of the
    order. Callers without an id use the first row with the admission time.
    """
    if not turn_instance_id or (not event_id and not recorded_at):
        return None
    for index, event in enumerate(events):
        if not _record_owned_by_goal_ref(event, owner_goal_ref):
            continue
        if (
            event.get("event_kind") == "quota_should_run"
            and event.get("goal_id") == goal_id
            and event.get("agent_id") == agent_id
            and event.get("run_id") == turn_instance_id
            and (
                event.get("event_id") == event_id
                if event_id
                else event.get("recorded_at") == recorded_at
            )
        ):
            return index
    return None


def _source_run(
    runs: Sequence[AckMapping] | None, *, turn_instance_id: str, obligation_id: str,
    owner_goal_ref: dict[str, str] | None,
) -> AckMapping:
    for run in runs or ():
        if not _record_owned_by_goal_ref(run, owner_goal_ref):
            continue
        if _binding_text(run.get("turn_instance_id")) != turn_instance_id:
            continue
        ack = run.get("autonomous_replan_ack")
        delta = ack.get("semantic_delta") if isinstance(ack, Mapping) else None
        if isinstance(delta, Mapping) and _binding_text(delta.get("obligation_id")) == obligation_id:
            return run
    return {}


def _expected_todo_receipt_from_owned_quota_events(
    run: AckMapping, quota_events: Sequence[AckMapping], *,
    goal_id: str, agent_id: str, turn_instance_id: str, obligation_id: str,
) -> tuple[str, str]:
    identity_raw = run.get("settlement_identity")
    identity: AckMapping = identity_raw if isinstance(identity_raw, Mapping) else {}
    todo = _binding_text(run.get("todo_id") or identity.get("todo_id"))
    effect = _binding_text(identity.get("effect_id"))
    if todo and effect:
        return todo, effect
    for event in quota_events:
        if (
            event.get("event_kind") != "quota_should_run"
            or event.get("goal_id") != goal_id
            or event.get("agent_id") != agent_id
            or _binding_text(event.get("run_id")) != turn_instance_id
        ):
            continue
        details = _event_details(event)
        receipt_replan = _binding_text(details.get("replan_obligation_id"))
        retained_todo = (
            _binding_text(details.get("pending_action_selection_todo_id"))
            if receipt_replan == obligation_id
            else ""
        )
        todo = todo or _binding_text(
            event.get("todo_id") or details.get("todo_id") or retained_todo
        )
        effect = effect or _binding_text(details.get("settlement_effect_id"))
    return todo, effect


def _expected_todo_receipt(
    run: AckMapping, events: Sequence[AckMapping], *,
    goal_id: str, agent_id: str, turn_instance_id: str, obligation_id: str,
    owner_goal_ref: dict[str, str] | None,
) -> tuple[str, str]:
    """Todo command identity for this ACK, filled from its heartbeat receipt.

    A historical index can omit the Todo and effect. The turn's existing
    quota receipt still has that exact identity.  A replan-bound receipt keeps
    the admitted Todo as its pending action selection; that retained selection
    may address the writeback without replacing the receipt's obligation.
    An empty or differently bound side is not a match.
    """
    return _expected_todo_receipt_from_owned_quota_events(
        run,
        [event for event in events if _record_owned_by_goal_ref(event, owner_goal_ref)],
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        obligation_id=obligation_id,
    )


def _writeback_event_is_bound(
    event: AckMapping, run: AckMapping, events: Sequence[AckMapping], *,
    goal_id: str, agent_id: str, obligation_id: str, turn_instance_id: str,
    owner_goal_ref: dict[str, str] | None,
) -> bool:
    """Require one positive identity shared with the accepted ACK.

    The event obligation must equal the semantic delta, or its Todo and effect
    must equal the ACK receipt. Missing fields do not count as agreement.
    """
    if not _record_owned_by_goal_ref(event, owner_goal_ref):
        return False
    if (
        event.get("event_kind") != "refresh_state"
        or event.get("goal_id") != goal_id
        or event.get("agent_id") != agent_id
        or _binding_text(event.get("run_id")) != turn_instance_id
    ):
        return False
    details = _event_details(event)
    if details.get("autonomous_replan_recorded") is not True:
        return False
    event_obligation = _binding_text(details.get("replan_obligation_id"))
    if event_obligation:
        return event_obligation == obligation_id
    event_todo = _binding_text(event.get("todo_id"))
    event_effect = _binding_text(details.get("settlement_effect_id"))
    if not event_todo or not event_effect:
        return False
    expected_todo, expected_effect = _expected_todo_receipt(
        run, events, goal_id=goal_id, agent_id=agent_id,
        turn_instance_id=turn_instance_id, obligation_id=obligation_id,
        owner_goal_ref=owner_goal_ref)
    return bool(
        expected_todo and expected_effect
        and event_todo == expected_todo and event_effect == expected_effect
    )


def _original_writeback_event_index(
    events: Sequence[AckMapping], runs: Sequence[AckMapping] | None, *,
    goal_id: str, agent_id: str, obligation_id: str, turn_instance_id: str,
    owner_goal_ref: dict[str, str] | None,
) -> int | None:
    if not turn_instance_id or not obligation_id:
        return None
    run = _source_run(
        runs, turn_instance_id=turn_instance_id, obligation_id=obligation_id,
        owner_goal_ref=owner_goal_ref)
    for index, event in enumerate(events):
        if _writeback_event_is_bound(
            event, run, events, goal_id=goal_id, agent_id=agent_id,
            obligation_id=obligation_id, turn_instance_id=turn_instance_id,
            owner_goal_ref=owner_goal_ref,
        ):
            return index
    return None


def _attach_writeback_order_owned(
    candidates: Sequence[_MutableRecordT], events: Sequence[AckMapping], *,
    goal_id: str, agent_id: str, obligation_id: str,
    guard_recorded_at: str | None, guard_turn_instance_id: str | None,
    owned_runs: Sequence[AckMapping],
    owner: dict[str, str] | None,
    admission_event_id: str | None = None,
) -> None:
    if not candidates:
        return
    guard_turn_id = _binding_text(guard_turn_instance_id)
    guard_stamp = _binding_text(guard_recorded_at)
    guard_event_id = _binding_text(admission_event_id) or None
    candidate_turns = {
        t_id for candidate in candidates
        if (t_id := _binding_text(candidate.get("turn_instance_id")))
    }

    admission_index: int | None = None
    quota_events_by_run: dict[str, list[AckMapping]] = {}
    refresh_events_by_run: dict[str, list[tuple[int, AckMapping]]] = {}
    for index, event in enumerate(events):
        if not isinstance(event, Mapping) or not _record_owned_by_goal_ref(event, owner):
            continue
        if event.get("goal_id") != goal_id or event.get("agent_id") != agent_id:
            continue
        kind = event.get("event_kind")
        run_id_raw = event.get("run_id")
        run_id_text = _binding_text(run_id_raw)
        if kind == "quota_should_run":
            if (
                admission_index is None
                and guard_turn_id
                and (guard_event_id is not None or guard_stamp)
                and run_id_raw == guard_turn_id
                and (
                    event.get("event_id") == guard_event_id
                    if guard_event_id
                    else event.get("recorded_at") == guard_stamp
                )
            ):
                admission_index = index
            if obligation_id and run_id_text in candidate_turns:
                quota_events_by_run.setdefault(run_id_text, []).append(event)
        elif (
            kind == "refresh_state"
            and obligation_id
            and run_id_text in candidate_turns
            and _event_details(event).get("autonomous_replan_recorded") is True
        ):
            refresh_events_by_run.setdefault(run_id_text, []).append((index, event))

    if not obligation_id or not candidate_turns:
        return
    source_runs_by_turn: dict[str, AckMapping] = {}
    for run in owned_runs:
        t_id = _binding_text(run.get("turn_instance_id"))
        if t_id not in candidate_turns or t_id in source_runs_by_turn:
            continue
        ack = run.get("autonomous_replan_ack")
        delta = ack.get("semantic_delta") if isinstance(ack, Mapping) else None
        if isinstance(delta, Mapping) and _binding_text(delta.get("obligation_id")) == obligation_id:
            source_runs_by_turn[t_id] = run

    receipt_by_turn: dict[str, tuple[str, str]] = {}
    for candidate in candidates:
        turn_id = _binding_text(candidate.get("turn_instance_id"))
        if not turn_id:
            continue
        matched: tuple[int, AckMapping] | None = None
        for index, event in refresh_events_by_run.get(turn_id, ()):
            details = _event_details(event)
            event_obligation = _binding_text(details.get("replan_obligation_id"))
            if event_obligation:
                if event_obligation == obligation_id:
                    matched = (index, event)
                    break
                continue
            event_todo = _binding_text(event.get("todo_id"))
            event_effect = _binding_text(details.get("settlement_effect_id"))
            if not event_todo or not event_effect:
                continue
            expected = receipt_by_turn.get(turn_id)
            if expected is None:
                expected = _expected_todo_receipt_from_owned_quota_events(
                    source_runs_by_turn.get(turn_id, {}),
                    quota_events_by_run.get(turn_id, ()),
                    goal_id=goal_id,
                    agent_id=agent_id,
                    turn_instance_id=turn_id,
                    obligation_id=obligation_id,
                )
                receipt_by_turn[turn_id] = expected
            if expected[0] and expected[1] and event_todo == expected[0] and event_effect == expected[1]:
                matched = (index, event)
                break
        if matched is None:
            continue
        evidence_index, matched_event = matched
        candidate["writeback_recorded_at"] = matched_event.get("recorded_at")
        candidate["writeback_goal_id"] = matched_event.get("goal_id")
        candidate["writeback_agent_id"] = matched_event.get("agent_id")
        candidate["writeback_turn_instance_id"] = matched_event.get("run_id")
        candidate["writeback_obligation_id"] = obligation_id
        candidate["writeback_follows_admission"] = (
            admission_index is not None and evidence_index > admission_index
        )


def attach_historical_ack_writeback_order(
    candidates: Sequence[_MutableRecordT], events: Sequence[AckMapping], *,
    goal_id: str, agent_id: str, obligation_id: str,
    guard_recorded_at: str | None, guard_turn_instance_id: str | None,
    runs: Sequence[AckMapping] | None = None,
    goal_ref: AckMapping | None = None,
    admission_event_id: str | None = None,
) -> None:
    """Copy the original refresh event order onto matching ACK candidates.

    The compact run clock is whole seconds. The decision owner compares this
    event with admission; this reader only selects the bound original event.
    """
    owner = _owner_goal_ref(goal_ref, goal_id=goal_id)
    _attach_writeback_order_owned(
        candidates,
        events,
        goal_id=goal_id,
        agent_id=agent_id,
        obligation_id=obligation_id,
        guard_recorded_at=guard_recorded_at,
        guard_turn_instance_id=guard_turn_instance_id,
        owned_runs=_owned_records(runs, owner),
        owner=owner,
        admission_event_id=admission_event_id,
    )
