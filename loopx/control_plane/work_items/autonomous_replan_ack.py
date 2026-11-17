from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from ..effect_runtime import effect_runtime_result
from ..runtime.time import parse_timestamp
from .progress_observation import FRESH_VISION_PATH_DISPOSITIONS

AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW = 20
AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT = 8
# A normal delivery appends both a durable run record and a neutral quota-spend
# record. Keep enough internal history to observe the full material-run
# threshold even when those records are interleaved, with headroom for other
# neutral events. The budget is per agent lane: a Goal-wide window that peer
# lanes also fill cannot decide one lane's material-run count.
AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK = AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW * 3


class SettlementHistoryQualification(NamedTuple):
    accepted_blocked_replans: frozenset[int]
    active_turn_retries: frozenset[int]


def qualify_settlement_history(
    runs: list[dict[str, Any]], *, observed_at: str | None = None,
) -> SettlementHistoryQualification:
    """Transport a history batch to its settlement owner, preserving originals.

    Candidate selection is an IO optimization, never positive qualification.
    The TS result selects existing rows; it cannot acknowledge or settle work.
    Missing legacy evidence remains an ordinary outcome checkpoint.
    """
    candidates = [index for index, run in enumerate(runs)
                  if run.get("replan_obligation_id")
                  or (observed_at is not None and "blocked_retry" in run)]
    if not candidates:
        return SettlementHistoryQualification(frozenset(), frozenset())
    fields = (
        "goal_id", "agent_id", "todo_id", "turn_instance_id", "replan_obligation_id",
        "settlement_identity", "delivery_outcome", "progress_observation",
        "autonomous_replan_ack", "vision_checkpoint", "blocked_retry",
    )
    result = effect_runtime_result("quota.settlement.read", {
        "schema_version": "loopx_quota_history_qualification_request_v0",
        "observed_at": observed_at,
        "runs": [{key: runs[index][key] for key in fields if key in runs[index]}
                 for index in candidates],
    }, large_local_snapshot=True)
    def indexes(field: str) -> frozenset[int]:
        values = result.get(field) if isinstance(result, dict) else None
        if not isinstance(values, list) or any(
            type(index) is not int or not 0 <= index < len(candidates) for index in values
        ):
            raise RuntimeError("TypeScript settlement history result must contain valid indexes")
        return frozenset(candidates[index] for index in values)

    return SettlementHistoryQualification(indexes("accepted_indexes"), indexes("active_retry_indexes"))


def accepted_blocked_replan_history_indexes(runs: list[dict[str, Any]]) -> frozenset[int]:
    """Legacy outcome reader needs administrative qualification, not a clock."""
    return qualify_settlement_history(runs).accepted_blocked_replans


def ack_binds_trigger_checkpoints(ack: dict[str, Any] | None) -> bool:
    """Return whether an ACK binds exact typed trigger revisions.

    A checkpoint-bearing ACK is revision-gated: the typed frontier evaluator
    suppresses a trigger only when the ACK names that trigger's exact revision,
    and the obligation-clearing path additionally requires the exact obligation
    id. Such an ACK therefore cannot hide a materially changed frontier, which
    is why it stays visible for the whole scanned history instead of expiring
    with the material-run window.
    """

    semantic_delta = ack.get("semantic_delta") if isinstance(ack, dict) else None
    if not isinstance(semantic_delta, dict):
        return False
    checkpoints = semantic_delta.get("trigger_checkpoints")
    if not isinstance(checkpoints, list):
        return False
    return any(
        isinstance(checkpoint, dict)
        and str(checkpoint.get("kind") or "").strip()
        and str(checkpoint.get("frontier_revision") or "").strip()
        for checkpoint in checkpoints
    )


def normalize_projected_autonomous_replan_ack(
    ack: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return only ACKs already carrying the current semantic contract."""

    if not isinstance(ack, dict) or ack.get("recorded") is not True:
        return None
    normalized = dict(ack)
    semantic_delta = normalized.get("semantic_delta")
    if not isinstance(semantic_delta, dict) or semantic_delta.get("accepted") is not True:
        return None
    return normalized


def autonomous_replan_ack_recorded(run: dict[str, Any]) -> bool:
    ack = run.get("autonomous_replan_ack")
    if not isinstance(ack, dict) or ack.get("recorded") is not True:
        return False
    normalized = normalize_projected_autonomous_replan_ack(ack)
    return bool(
        isinstance(normalized, dict)
        and isinstance(normalized.get("semantic_delta"), dict)
        and normalized["semantic_delta"].get("accepted") is True
    )


def watch_lane_continuation_todo_ids(
    delta_contract: dict[str, Any] | None,
) -> list[str]:
    """Return the exact Todo evidence carried by a validated watch delta."""

    if not isinstance(delta_contract, dict):
        return []
    todo_ids: list[str] = []
    for item in delta_contract.get("auto_evidence") or []:
        if not isinstance(item, dict) or item.get("kind") != "watch_lane_continuation":
            continue
        for raw_todo_id in item.get("todo_ids") or []:
            todo_id = str(raw_todo_id or "").strip()
            if todo_id and todo_id not in todo_ids:
                todo_ids.append(todo_id)
    return todo_ids


def compact_autonomous_replan_ack(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(run, dict) or not autonomous_replan_ack_recorded(run):
        return None
    ack = run.get("autonomous_replan_ack")
    if not isinstance(ack, dict):
        return None
    normalized_ack = normalize_projected_autonomous_replan_ack(ack)
    if normalized_ack is None:
        return None
    semantic_delta = normalized_ack["semantic_delta"]
    result = {
        "schema_version": ack.get("schema_version"),
        "recorded": True,
        "source": ack.get("source"),
        "semantic_delta": {
            field: semantic_delta.get(field)
            for field in (
                "schema_version",
                "accepted",
                "outcomes",
                "satisfying_outcomes",
                "required_any_of",
                "trigger_kinds",
                "trigger_checkpoints",
                "obligation_id",
                "successor_todo_id",
                "successor_origin_obligation_id",
                "successor_binding",
                "observation_fingerprint",
                "reason",
            )
            if semantic_delta.get(field) is not None
        },
    }
    outcomes = semantic_delta.get("outcomes")
    if (
        isinstance(outcomes, list)
        and "fresh_vision_path_outcome" in outcomes
    ):
        raw_vision = run.get("agent_vision")
        agent_vision = raw_vision if isinstance(raw_vision, dict) else {}
        raw_path = agent_vision.get("path_delta")
        path_delta = raw_path if isinstance(raw_path, dict) else {}
        path_disposition = str(path_delta.get("outcome") or "").strip()
        if path_disposition in FRESH_VISION_PATH_DISPOSITIONS:
            result["path_disposition"] = path_disposition
    delta_contract = ack.get("delta_contract")
    if isinstance(delta_contract, dict):
        result["delta_contract"] = {
            "schema_version": delta_contract.get("schema_version"),
            "delta_present": bool(delta_contract.get("delta_present")),
            "delta_kinds": [
                str(item)
                for item in (delta_contract.get("delta_kinds") or [])
                if str(item or "").strip()
            ],
            "auto_evidence": [
                dict(item)
                for item in (delta_contract.get("auto_evidence") or [])
                if isinstance(item, dict)
            ][:6],
        }
    goal_id = str(run.get("goal_id") or "").strip()
    if goal_id:
        result["goal_id"] = goal_id
    agent_id = str(run.get("agent_id") or "").strip()
    if agent_id:
        result["agent_id"] = agent_id
    turn_instance_id = str(run.get("turn_instance_id") or "").strip()
    if turn_instance_id:
        result["turn_instance_id"] = turn_instance_id
    frontier_identity = str(ack.get("frontier_identity") or "").strip()
    if frontier_identity:
        result["frontier_identity"] = frontier_identity
    generated_at = str(run.get("generated_at") or "").strip()
    if generated_at:
        result["generated_at"] = generated_at
    todo_source = run.get("todo_source", normalized_ack.get("todo_source"))
    if isinstance(todo_source, dict):
        # Transport the original provider anchor, never a current-head alias.
        # The canonical source reader validates it before legacy ACK recovery.
        result["todo_source"] = dict(todo_source)
    return result


def autonomous_replan_ack_candidates_for_obligation(
    newest_first_runs: list[dict[str, Any]] | None,
    *,
    goal_id: str | None,
    agent_id: str | None,
    obligation_id: str | None,
    limit: int = AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """Project a bounded set of exact durable ACK candidates for recovery.

    This lookup prevents a newer unrelated ACK from hiding an older ACK for a
    receipt-bound obligation.  It only narrows journal IO by stable identity;
    the TypeScript settlement owner still validates schema, outcomes,
    chronology, and the distinct durable Turn before accepting any candidate.
    """

    safe_goal_id = str(goal_id or "").strip()
    safe_agent_id = str(agent_id or "").strip()
    safe_obligation_id = str(obligation_id or "").strip()
    if not safe_goal_id or not safe_agent_id or not safe_obligation_id or limit <= 0:
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for run in newest_first_runs or []:
        compact = compact_autonomous_replan_ack(run)
        if not isinstance(compact, dict):
            continue
        semantic_delta = compact.get("semantic_delta")
        if not isinstance(semantic_delta, dict):
            continue
        if (
            compact.get("goal_id") != safe_goal_id
            or compact.get("agent_id") != safe_agent_id
            or semantic_delta.get("obligation_id") != safe_obligation_id
        ):
            continue
        identity = (
            str(compact.get("turn_instance_id") or "").strip(),
            str(compact.get("generated_at") or "").strip(),
            safe_obligation_id,
        )
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(compact)
        if len(candidates) >= limit:
            break
    return candidates


def _event_details(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details")
    return details if isinstance(details, dict) else {}


def _admission_event_index(
    events: list[dict[str, Any]], *, goal_id: str, agent_id: str,
    turn_instance_id: str, recorded_at: str,
) -> int | None:
    if not turn_instance_id or not recorded_at:
        return None
    found: int | None = None
    for index, event in enumerate(events):
        if (
            event.get("event_kind") == "quota_should_run"
            and event.get("goal_id") == goal_id
            and event.get("agent_id") == agent_id
            and event.get("run_id") == turn_instance_id
            and event.get("recorded_at") == recorded_at
        ):
            found = index
    return found


def _binding_text(value: Any) -> str:
    return str(value or "").strip()


def _source_run(
    runs: list[dict[str, Any]] | None, *, turn_instance_id: str, obligation_id: str,
) -> dict[str, Any]:
    for run in runs or []:
        if _binding_text(run.get("turn_instance_id")) != turn_instance_id:
            continue
        ack = run.get("autonomous_replan_ack")
        delta = ack.get("semantic_delta") if isinstance(ack, dict) else None
        if isinstance(delta, dict) and _binding_text(delta.get("obligation_id")) == obligation_id:
            return run
    return {}


def _expected_todo_receipt(
    run: dict[str, Any], events: list[dict[str, Any]], *,
    goal_id: str, agent_id: str, turn_instance_id: str, obligation_id: str,
) -> tuple[str, str]:
    """Todo command identity for this ACK, filled from its heartbeat receipt.

    A historical index can omit the Todo and effect. The turn's existing
    quota receipt still has that exact identity.  A replan-bound receipt keeps
    the admitted Todo as its pending action selection; that retained selection
    may address the writeback without replacing the receipt's obligation.
    An empty or differently bound side is not a match.
    """

    identity = run.get("settlement_identity")
    identity = identity if isinstance(identity, dict) else {}
    todo = _binding_text(run.get("todo_id") or identity.get("todo_id"))
    effect = _binding_text(identity.get("effect_id"))
    if todo and effect:
        return todo, effect
    for event in events:
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


def _writeback_event_is_bound(
    event: dict[str, Any], run: dict[str, Any], events: list[dict[str, Any]], *,
    goal_id: str, agent_id: str, obligation_id: str, turn_instance_id: str,
) -> bool:
    """Require one positive identity shared with the accepted ACK.

    The event obligation must equal the semantic delta, or its Todo and effect
    must equal the ACK receipt. Missing fields do not count as agreement.
    """

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
        turn_instance_id=turn_instance_id, obligation_id=obligation_id)
    return bool(
        expected_todo and expected_effect
        and event_todo == expected_todo and event_effect == expected_effect
    )


def _original_writeback_event_index(
    events: list[dict[str, Any]], runs: list[dict[str, Any]] | None, *,
    goal_id: str, agent_id: str, obligation_id: str, turn_instance_id: str,
) -> int | None:
    if not turn_instance_id or not obligation_id:
        return None
    run = _source_run(runs, turn_instance_id=turn_instance_id, obligation_id=obligation_id)
    for index, event in enumerate(events):
        if _writeback_event_is_bound(
            event, run, events, goal_id=goal_id, agent_id=agent_id,
            obligation_id=obligation_id, turn_instance_id=turn_instance_id,
        ):
            return index
    return None


def attach_historical_ack_writeback_order(
    candidates: list[dict[str, Any]], events: list[dict[str, Any]], *,
    goal_id: str, agent_id: str, obligation_id: str,
    guard_recorded_at: str | None, guard_turn_instance_id: str | None,
    runs: list[dict[str, Any]] | None = None,
) -> None:
    """Copy the original refresh event order onto matching ACK candidates.

    The compact run clock is whole seconds. The decision owner compares this
    event with admission; this reader only selects the bound original event.
    """

    admission_index = _admission_event_index(
        events, goal_id=goal_id, agent_id=agent_id,
        turn_instance_id=str(guard_turn_instance_id or "").strip(),
        recorded_at=str(guard_recorded_at or "").strip(),
    )
    for candidate in candidates:
        turn_instance_id = _binding_text(candidate.get("turn_instance_id"))
        evidence_index = _original_writeback_event_index(
            events, runs, goal_id=goal_id, agent_id=agent_id, obligation_id=obligation_id,
            turn_instance_id=turn_instance_id,
        )
        if evidence_index is None:
            continue
        event = events[evidence_index]
        candidate["writeback_recorded_at"] = event.get("recorded_at")
        candidate["writeback_goal_id"] = event.get("goal_id")
        candidate["writeback_agent_id"] = event.get("agent_id")
        candidate["writeback_turn_instance_id"] = event.get("run_id")
        candidate["writeback_obligation_id"] = obligation_id
        candidate["writeback_follows_admission"] = (
            admission_index is not None and evidence_index > admission_index
        )


def historical_ack_candidates_with_writeback_order(
    newest_first_runs: list[dict[str, Any]] | None, *,
    goal_id: str, agent_id: str, obligation_id: str | None,
    guard_scoped: bool = True, runtime_root: Path | str | None = None,
    source_route: Any = None,
    guard_recorded_at: str | None = None, guard_turn_instance_id: str | None = None,
) -> list[dict[str, Any]]:
    """Project obligation candidates and attach their original writeback order."""
    from ...rollout_event_log import load_rollout_events, rollout_event_log_path

    selected = _binding_text(obligation_id) if guard_scoped else ""
    candidates = autonomous_replan_ack_candidates_for_obligation(
        newest_first_runs, goal_id=goal_id, agent_id=agent_id, obligation_id=selected or None)
    route = source_route if isinstance(source_route, dict) else {}
    root_text = runtime_root or route.get("runtime_root")
    root = Path(str(root_text)) if root_text else None
    if root is None or not selected:
        return candidates
    attach_historical_ack_writeback_order(
        candidates, load_rollout_events(rollout_event_log_path(root, goal_id)),
        goal_id=goal_id, agent_id=agent_id, obligation_id=selected,
        guard_recorded_at=guard_recorded_at, guard_turn_instance_id=guard_turn_instance_id,
        runs=newest_first_runs,
    )
    return candidates


def load_receipt_replan_ack_candidates(
    runtime_root: Path, *, goal_id: str, agent_id: str, obligation_id: str,
    guard_recorded_at: str | None = None, guard_turn_instance_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read exact recovery candidates without widening ordinary status history."""
    from ...history import load_index
    from ..runtime.time import chronology_key

    runs, _ = load_index(runtime_root / "goals" / goal_id / "runs" / "index.jsonl")
    newest_first = [run for _, run in sorted(enumerate(runs),
        key=lambda item: (*chronology_key(item[1].get("generated_at")), item[0]), reverse=True)]
    return historical_ack_candidates_with_writeback_order(
        newest_first, goal_id=goal_id, agent_id=agent_id, obligation_id=obligation_id,
        runtime_root=runtime_root, guard_recorded_at=guard_recorded_at,
        guard_turn_instance_id=guard_turn_instance_id,
    )


def latest_blocked_successor_frontier_identity(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_id: str | None = None,
) -> str | None:
    return _latest_monitor_replan_frontier_identity(
        latest_runs,
        agent_id=agent_id,
        include_generic_watch=False,
    )


def latest_monitor_replan_frontier_identity(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_id: str | None = None,
    watch_todo_ids: list[str] | None = None,
) -> str | None:
    """Return the latest monitor identity that a durable replan ACK must bind."""

    return _latest_monitor_replan_frontier_identity(
        latest_runs,
        agent_id=agent_id,
        include_generic_watch=True,
        watch_todo_ids=watch_todo_ids,
    )


def _latest_monitor_replan_frontier_identity(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_id: str | None,
    include_generic_watch: bool,
    watch_todo_ids: list[str] | None = None,
) -> str | None:
    normalized_agent_id = str(agent_id or "").strip()
    normalized_watch_todo_ids = {
        str(todo_id or "").strip()
        for todo_id in (watch_todo_ids or [])
        if str(todo_id or "").strip()
    }
    evidence_linked_watch = (
        include_generic_watch and len(normalized_watch_todo_ids) == 1
    )
    intervening_material_runs = 0
    for run in latest_runs or []:
        if not isinstance(run, dict):
            continue
        raw_target = run.get("monitor_target")
        target = raw_target if isinstance(raw_target, dict) else {}
        if normalized_agent_id:
            run_agent_id = str(run.get("agent_id") or "").strip()
            target_agent_id = str(target.get("agent_id") or "").strip()
            if run_agent_id and target_agent_id and run_agent_id != target_agent_id:
                if normalized_agent_id in {run_agent_id, target_agent_id}:
                    return None
                continue
            attributed_agent_id = run_agent_id or target_agent_id
            if not attributed_agent_id:
                return None
            if attributed_agent_id != normalized_agent_id:
                continue
        classification = str(run.get("classification") or "").strip()
        if classification != "quota_monitor_poll":
            if evidence_linked_watch:
                intervening_material_runs += 1
                if (
                    intervening_material_runs
                    >= AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW
                ):
                    return None
                continue
            return None
        if target.get("monitor_mode") != (
            "blocked_successor_wait_without_material_transition"
        ):
            if not (
                include_generic_watch
                and target.get("monitor_mode")
                == "monitor_quiet_until_material_transition"
            ):
                continue
            target_identity = str(target.get("target_id") or "").strip()
            return target_identity or None
        if intervening_material_runs:
            return None
        frontier_identity = str(target.get("frontier_identity") or "").strip()
        return frontier_identity or None
    return None


def autonomous_replan_ack_matches_frontier(
    ack: dict[str, Any] | None,
    obligation: dict[str, Any] | None,
) -> bool:
    if not isinstance(obligation, dict):
        return True
    frontier_identity = str(obligation.get("frontier_identity") or "").strip()
    if not frontier_identity:
        return True
    if not isinstance(ack, dict):
        return False
    if str(ack.get("frontier_identity") or "").strip() != frontier_identity:
        return False

    ack_generated_at = parse_timestamp(
        ack.get("generated_at") or ack.get("recorded_at")
    )
    trigger_times = [
        parsed
        for trigger in obligation.get("triggers") or []
        if isinstance(trigger, dict)
        if (
            parsed := parse_timestamp(
                trigger.get("latest_generated_at") or trigger.get("generated_at")
            )
        )
        is not None
    ]
    if ack_generated_at is not None and trigger_times:
        return ack_generated_at >= max(trigger_times)
    return True


def autonomous_replan_ack_matches_agent(
    ack: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> bool:
    if not isinstance(ack, dict):
        return False
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        return True
    ack_agent_id = str(ack.get("agent_id") or "").strip()
    return bool(ack_agent_id and ack_agent_id == normalized_agent_id)


def latest_autonomous_replan_ack_for_projection(
    latest_runs: list[dict[str, Any]] | None,
    *,
    neutral_classifications: set[str],
) -> dict[str, Any] | None:
    """Return the newest durable replan ACK this lane may still use.

    A legacy ACK (one without typed trigger checkpoints) keeps the material-run
    review window that produced it. A checkpoint-bearing ACK stays visible while
    the scanned history still carries it: expiring it on run count alone re-armed
    the long Todo-chain rule for an unchanged frontier, which is exactly the
    level trigger the checkpoint contract exists to prevent.
    """

    material_run_count = 0
    window_expired = False
    for run in latest_runs or []:
        if not isinstance(run, dict):
            continue
        replan_ack = compact_autonomous_replan_ack(run)
        if replan_ack:
            if not window_expired or ack_binds_trigger_checkpoints(replan_ack):
                return replan_ack
            return None
        classification = str(run.get("classification") or "").strip()
        if not classification:
            continue
        if classification in neutral_classifications:
            continue
        if classification == "quota_monitor_poll":
            continue
        material_run_count += 1
        if material_run_count >= AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW:
            window_expired = True
    return None
    return None
