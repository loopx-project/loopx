from __future__ import annotations

from typing import Any

from ..runtime.time import parse_timestamp
from .progress_observation import FRESH_VISION_PATH_DISPOSITIONS

AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW = 20
# A normal delivery appends both a durable run record and a neutral quota-spend
# record. Keep enough internal history to observe the full material-run
# threshold even when those records are interleaved, with headroom for other
# neutral events. The budget is per agent lane: a Goal-wide window that peer
# lanes also fill cannot decide one lane's material-run count.
AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK = AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW * 3


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
    agent_id = str(run.get("agent_id") or "").strip()
    if agent_id:
        result["agent_id"] = agent_id
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
