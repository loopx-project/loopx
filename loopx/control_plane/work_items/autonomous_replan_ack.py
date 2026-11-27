"""Compact autonomous replan ACK normalization, candidate lookup, and frontier helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from ..effect_runtime import effect_runtime_result as effect_runtime_result
from ..runtime.time import parse_timestamp
from .autonomous_replan_ack_history import (
    AckMapping,
    AckObject,
    _admission_event_index as _admission_event_index,
    _attach_writeback_order_owned,
    _binding_text as _binding_text,
    _event_details as _event_details,
    _expected_todo_receipt as _expected_todo_receipt,
    _original_writeback_event_index as _original_writeback_event_index,
    _owned_records as _owned_records,
    _owner_goal_ref as _owner_goal_ref,
    _parsed_exact_goal_ref as _parsed_exact_goal_ref,
    _record_owned_by_goal_ref as _record_owned_by_goal_ref,
    _source_run as _source_run,
    _writeback_event_is_bound as _writeback_event_is_bound,
    attach_historical_ack_writeback_order as attach_historical_ack_writeback_order,
    drop_unowned_recorded_replan_acks as drop_unowned_recorded_replan_acks,
)
from .progress_observation import FRESH_VISION_PATH_DISPOSITIONS

AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW = 20
AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT = 8
# A normal delivery appends both a durable run record and a neutral quota-spend
# record. Keep enough internal history to observe the full material-run
# threshold even when those records are interleaved, with headroom for other
# neutral events. The budget is per agent lane: a Goal-wide window that peer
# lanes also fill cannot decide one lane's material-run count.
AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK = AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW * 3

_COMPACT_SEMANTIC_DELTA_FIELDS = (
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


class SettlementHistoryQualification(NamedTuple):
    accepted_blocked_replans: frozenset[int]
    active_turn_retries: frozenset[int]


def _accepted_semantic_delta(ack: object) -> AckMapping | None:
    if not isinstance(ack, Mapping) or ack.get("recorded") is not True:
        return None
    semantic_delta = ack.get("semantic_delta")
    if not isinstance(semantic_delta, Mapping) or semantic_delta.get("accepted") is not True:
        return None
    return semantic_delta


def qualify_settlement_history(
    runs: Sequence[AckMapping], *, observed_at: str | None = None,
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
    result_map: AckMapping = result if isinstance(result, Mapping) else {}

    def indexes(field: str) -> frozenset[int]:
        values = result_map.get(field)
        if not isinstance(values, list):
            raise RuntimeError("TypeScript settlement history result must contain valid indexes")
        resolved: list[int] = []
        for index in values:
            if not isinstance(index, int) or type(index) is not int or not 0 <= index < len(candidates):
                raise RuntimeError("TypeScript settlement history result must contain valid indexes")
            resolved.append(candidates[index])
        return frozenset(resolved)

    return SettlementHistoryQualification(indexes("accepted_indexes"), indexes("active_retry_indexes"))


def accepted_blocked_replan_history_indexes(runs: Sequence[AckMapping]) -> frozenset[int]:
    """Legacy outcome reader needs administrative qualification, not a clock."""
    return qualify_settlement_history(runs).accepted_blocked_replans


def ack_binds_trigger_checkpoints(ack: AckMapping | None) -> bool:
    """Return whether an ACK binds exact typed trigger revisions.

    A checkpoint-bearing ACK is revision-gated: the typed frontier evaluator
    suppresses a trigger only when the ACK names that trigger's exact revision,
    and the obligation-clearing path additionally requires the exact obligation
    id. Such an ACK therefore cannot hide a materially changed frontier, which
    is why it stays visible for the whole scanned history instead of expiring
    with the material-run window.
    """
    semantic_delta = ack.get("semantic_delta") if isinstance(ack, Mapping) else None
    if not isinstance(semantic_delta, Mapping):
        return False
    checkpoints = semantic_delta.get("trigger_checkpoints")
    if not isinstance(checkpoints, list):
        return False
    return any(
        isinstance(checkpoint, Mapping)
        and _binding_text(checkpoint.get("kind"))
        and _binding_text(checkpoint.get("frontier_revision"))
        for checkpoint in checkpoints
    )


def normalize_projected_autonomous_replan_ack(
    ack: AckMapping | None,
) -> AckObject | None:
    """Return only ACKs already carrying the current semantic contract."""
    if _accepted_semantic_delta(ack) is None or not isinstance(ack, Mapping):
        return None
    return dict(ack)


def autonomous_replan_ack_recorded(run: AckMapping) -> bool:
    return _accepted_semantic_delta(run.get("autonomous_replan_ack")) is not None


def watch_lane_continuation_todo_ids(
    delta_contract: AckMapping | None,
) -> list[str]:
    """Return the exact Todo evidence carried by a validated watch delta."""
    if not isinstance(delta_contract, Mapping):
        return []
    todo_ids: list[str] = []
    auto_evidence = delta_contract.get("auto_evidence")
    if not isinstance(auto_evidence, Sequence) or isinstance(auto_evidence, (str, bytes)):
        return todo_ids
    for item in auto_evidence:
        if not isinstance(item, Mapping) or item.get("kind") != "watch_lane_continuation":
            continue
        raw_todo_ids = item.get("todo_ids")
        if not isinstance(raw_todo_ids, Sequence) or isinstance(raw_todo_ids, (str, bytes)):
            continue
        for raw_todo_id in raw_todo_ids:
            todo_id = _binding_text(raw_todo_id)
            if todo_id and todo_id not in todo_ids:
                todo_ids.append(todo_id)
    return todo_ids


def compact_autonomous_replan_ack(run: AckMapping | None) -> AckObject | None:
    if not isinstance(run, Mapping):
        return None
    raw_ack = run.get("autonomous_replan_ack")
    semantic_delta = _accepted_semantic_delta(raw_ack)
    if semantic_delta is None or not isinstance(raw_ack, Mapping):
        return None
    result: AckObject = {
        "schema_version": raw_ack.get("schema_version"),
        "recorded": True,
        "source": raw_ack.get("source"),
        "semantic_delta": {
            field: semantic_delta.get(field)
            for field in _COMPACT_SEMANTIC_DELTA_FIELDS
            if semantic_delta.get(field) is not None
        },
    }
    outcomes = semantic_delta.get("outcomes")
    if isinstance(outcomes, list) and "fresh_vision_path_outcome" in outcomes:
        raw_vision = run.get("agent_vision")
        agent_vision: AckMapping = raw_vision if isinstance(raw_vision, Mapping) else {}
        raw_path = agent_vision.get("path_delta")
        path_delta: AckMapping = raw_path if isinstance(raw_path, Mapping) else {}
        path_disposition = _binding_text(path_delta.get("outcome"))
        if path_disposition in FRESH_VISION_PATH_DISPOSITIONS:
            result["path_disposition"] = path_disposition
    delta_contract = raw_ack.get("delta_contract")
    if isinstance(delta_contract, Mapping):
        raw_kinds = delta_contract.get("delta_kinds")
        raw_evidence = delta_contract.get("auto_evidence")
        result["delta_contract"] = {
            "schema_version": delta_contract.get("schema_version"),
            "delta_present": bool(delta_contract.get("delta_present")),
            "delta_kinds": [
                str(item)
                for item in (raw_kinds if isinstance(raw_kinds, list) else ())
                if _binding_text(item)
            ],
            "auto_evidence": [
                dict(item)
                for item in (raw_evidence if isinstance(raw_evidence, list) else ())
                if isinstance(item, Mapping)
            ][:6],
        }
    for key, source in (
        ("goal_id", run),
        ("agent_id", run),
        ("turn_instance_id", run),
        ("frontier_identity", raw_ack),
        ("generated_at", run),
    ):
        text = _binding_text(source.get(key))
        if text:
            result[key] = text
    todo_source = run.get("todo_source", raw_ack.get("todo_source"))
    if isinstance(todo_source, Mapping):
        # Transport the original provider anchor, never a current-head alias.
        # The canonical source reader validates it before legacy ACK recovery.
        result["todo_source"] = dict(todo_source)
    return result


def autonomous_replan_ack_candidates_for_obligation(
    newest_first_runs: Sequence[AckMapping] | None,
    *,
    goal_id: str | None,
    agent_id: str | None,
    obligation_id: str | None,
    limit: int = AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT,
) -> list[AckObject]:
    """Project a bounded set of exact durable ACK candidates for recovery.

    This lookup prevents a newer unrelated ACK from hiding an older ACK for a
    receipt-bound obligation.  It only narrows journal IO by stable identity;
    the TypeScript settlement owner still validates schema, outcomes,
    chronology, and the distinct durable Turn before accepting any candidate.
    """
    safe_goal_id = _binding_text(goal_id)
    safe_agent_id = _binding_text(agent_id)
    safe_obligation_id = _binding_text(obligation_id)
    if not safe_goal_id or not safe_agent_id or not safe_obligation_id or limit <= 0:
        return []
    candidates: list[AckObject] = []
    seen: set[tuple[str, str, str]] = set()
    for run in newest_first_runs or ():
        compact = compact_autonomous_replan_ack(run)
        if compact is None:
            continue
        semantic_delta = compact.get("semantic_delta")
        if not isinstance(semantic_delta, Mapping):
            continue
        if (
            compact.get("goal_id") != safe_goal_id
            or compact.get("agent_id") != safe_agent_id
            or semantic_delta.get("obligation_id") != safe_obligation_id
        ):
            continue
        identity = (
            _binding_text(compact.get("turn_instance_id")),
            _binding_text(compact.get("generated_at")),
            safe_obligation_id,
        )
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(compact)
        if len(candidates) >= limit:
            break
    return candidates


def historical_ack_candidates_with_writeback_order(
    newest_first_runs: Sequence[AckMapping] | None, *,
    goal_id: str, agent_id: str, obligation_id: str | None,
    guard_scoped: bool = True, runtime_root: Path | str | None = None,
    source_route: object = None,
    guard_recorded_at: str | None = None, guard_turn_instance_id: str | None = None,
    goal_ref: AckMapping | None = None,
    admission_event_id: str | None = None,
) -> list[AckObject]:
    """Project obligation candidates and attach their original writeback order."""
    from ...rollout_event_log import load_rollout_events, rollout_event_log_path

    owner = _owner_goal_ref(goal_ref, goal_id=goal_id)
    owned_runs = _owned_records(newest_first_runs, owner)
    selected = _binding_text(obligation_id) if guard_scoped else ""
    candidates = autonomous_replan_ack_candidates_for_obligation(
        owned_runs, goal_id=goal_id, agent_id=agent_id, obligation_id=selected or None)
    route: AckMapping = source_route if isinstance(source_route, Mapping) else {}
    root_text = runtime_root or route.get("runtime_root")
    root = Path(str(root_text)) if root_text else None
    if root is None or not selected:
        return candidates
    _attach_writeback_order_owned(
        candidates, load_rollout_events(rollout_event_log_path(root, goal_id)),
        goal_id=goal_id, agent_id=agent_id, obligation_id=selected,
        guard_recorded_at=guard_recorded_at, guard_turn_instance_id=guard_turn_instance_id,
        owned_runs=owned_runs, owner=owner, admission_event_id=admission_event_id,
    )
    return candidates


def load_receipt_replan_ack_candidates(
    runtime_root: Path, *, goal_id: str, agent_id: str, obligation_id: str,
    guard_recorded_at: str | None = None, guard_turn_instance_id: str | None = None,
    goal_ref: AckMapping | None = None,
    admission_event_id: str | None = None,
) -> list[AckObject]:
    """Read exact recovery candidates without widening ordinary status history."""
    from ...history import load_index
    from ..runtime.time import chronology_key

    runs, _ = load_index(runtime_root / "goals" / goal_id / "runs" / "index.jsonl")
    newest_first = [run for _, run in sorted(enumerate(runs),
        key=lambda item: (*chronology_key(item[1].get("generated_at")), item[0]), reverse=True)]
    return historical_ack_candidates_with_writeback_order(
        newest_first, goal_id=goal_id, agent_id=agent_id, obligation_id=obligation_id,
        runtime_root=runtime_root, guard_recorded_at=guard_recorded_at,
        guard_turn_instance_id=guard_turn_instance_id, goal_ref=goal_ref,
        admission_event_id=admission_event_id,
    )


def latest_blocked_successor_frontier_identity(
    latest_runs: Sequence[AckMapping] | None,
    *,
    agent_id: str | None = None,
) -> str | None:
    return _latest_monitor_replan_frontier_identity(
        latest_runs,
        agent_id=agent_id,
        include_generic_watch=False,
    )


def latest_monitor_replan_frontier_identity(
    latest_runs: Sequence[AckMapping] | None,
    *,
    agent_id: str | None = None,
    watch_todo_ids: Sequence[str] | None = None,
) -> str | None:
    """Return the latest monitor identity that a durable replan ACK must bind."""
    return _latest_monitor_replan_frontier_identity(
        latest_runs,
        agent_id=agent_id,
        include_generic_watch=True,
        watch_todo_ids=watch_todo_ids,
    )


def _latest_monitor_replan_frontier_identity(
    latest_runs: Sequence[AckMapping] | None,
    *,
    agent_id: str | None,
    include_generic_watch: bool,
    watch_todo_ids: Sequence[str] | None = None,
) -> str | None:
    normalized_agent_id = _binding_text(agent_id)
    normalized_watch_todo_ids = {
        stripped for todo_id in (watch_todo_ids or ()) if (stripped := _binding_text(todo_id))
    }
    evidence_linked_watch = include_generic_watch and len(normalized_watch_todo_ids) == 1
    intervening_material_runs = 0
    for run in latest_runs or ():
        if not isinstance(run, Mapping):
            continue
        raw_target = run.get("monitor_target")
        target: AckMapping = raw_target if isinstance(raw_target, Mapping) else {}
        if normalized_agent_id:
            run_agent_id = _binding_text(run.get("agent_id"))
            target_agent_id = _binding_text(target.get("agent_id"))
            if run_agent_id and target_agent_id and run_agent_id != target_agent_id:
                if normalized_agent_id in {run_agent_id, target_agent_id}:
                    return None
                continue
            attributed_agent_id = run_agent_id or target_agent_id
            if not attributed_agent_id:
                return None
            if attributed_agent_id != normalized_agent_id:
                continue
        classification = _binding_text(run.get("classification"))
        if classification != "quota_monitor_poll":
            if evidence_linked_watch:
                intervening_material_runs += 1
                if intervening_material_runs >= AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW:
                    return None
                continue
            return None
        if target.get("monitor_mode") != "blocked_successor_wait_without_material_transition":
            if not (
                include_generic_watch
                and target.get("monitor_mode") == "monitor_quiet_until_material_transition"
            ):
                continue
            return _binding_text(target.get("target_id")) or None
        if intervening_material_runs:
            return None
        return _binding_text(target.get("frontier_identity")) or None
    return None


def autonomous_replan_ack_matches_frontier(
    ack: AckMapping | None,
    obligation: AckMapping | None,
) -> bool:
    if not isinstance(obligation, Mapping):
        return True
    frontier_identity = _binding_text(obligation.get("frontier_identity"))
    if not frontier_identity:
        return True
    if not isinstance(ack, Mapping):
        return False
    if _binding_text(ack.get("frontier_identity")) != frontier_identity:
        return False
    ack_generated_at = parse_timestamp(ack.get("generated_at") or ack.get("recorded_at"))
    raw_triggers = obligation.get("triggers")
    trigger_times = [
        parsed
        for trigger in (raw_triggers if isinstance(raw_triggers, list) else ())
        if isinstance(trigger, Mapping)
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
    ack: AckMapping | None,
    *,
    agent_id: str | None,
) -> bool:
    if not isinstance(ack, Mapping):
        return False
    normalized_agent_id = _binding_text(agent_id)
    if not normalized_agent_id:
        return True
    ack_agent_id = _binding_text(ack.get("agent_id"))
    return bool(ack_agent_id and ack_agent_id == normalized_agent_id)


def latest_autonomous_replan_ack_for_projection(
    latest_runs: Sequence[AckMapping] | None,
    *,
    neutral_classifications: set[str],
) -> AckObject | None:
    """Return the newest durable replan ACK this lane may still use.

    A legacy ACK (one without typed trigger checkpoints) keeps the material-run
    review window that produced it. A checkpoint-bearing ACK stays visible while
    the scanned history still carries it: expiring it on run count alone re-armed
    the long Todo-chain rule for an unchanged frontier, which is exactly the
    level trigger the checkpoint contract exists to prevent.
    """
    material_run_count = 0
    window_expired = False
    for run in latest_runs or ():
        if not isinstance(run, Mapping):
            continue
        replan_ack = compact_autonomous_replan_ack(run)
        if replan_ack:
            if not window_expired or ack_binds_trigger_checkpoints(replan_ack):
                return replan_ack
            return None
        classification = _binding_text(run.get("classification"))
        if not classification or classification in neutral_classifications or classification == "quota_monitor_poll":
            continue
        material_run_count += 1
        if material_run_count >= AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW:
            window_expired = True
    return None
