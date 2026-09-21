from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Callable, Mapping
from typing import Any

from ..runtime.time import parse_timestamp
from ..todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_replan_obligation_id,
)
from ..todos.resume_planning import project_todo_resume_planning
from .progress_observation import replan_writeback_requirements, typed_progress_repeat_trigger
from .replan_settlement import (
    project_todo_lifecycle_settlement_reentry as project_todo_lifecycle_reentry_effect,
)


PublicSafeText = Callable[..., str | None]
AckRecorded = Callable[[dict[str, Any]], bool]


MAX_AUTONOMOUS_REPLAN_TRIGGERS = 3
AUTONOMOUS_REPLAN_STALL_THRESHOLD = 2
REPLAN_NOVELTY_POLICY_SCHEMA_VERSION = "replan_evidence_delivery_policy_v0"
TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE = "todo_lifecycle_settlement"
REPLAN_NOVELTY_GUIDANCE = (
    " Use the host-projected coverage ledger and produce a typed semantic delta; "
    "repeated observations cannot close replan."
)


def build_replan_novelty_policy() -> dict[str, str]:
    """Bind replan evidence delivery to the host-projected ledger."""

    return {
        "schema_version": REPLAN_NOVELTY_POLICY_SCHEMA_VERSION,
        "evidence_source": "agent_scoped_evidence_log",
        "delivery": "host_projected",
        "writeback": "typed_semantic_delta",
    }


def with_replan_novelty_guidance(action: str) -> str:
    """Make the policy visible once on every replan primary action."""

    marker = "host-projected coverage ledger"
    normalized = str(action or "").strip()
    if marker in normalized:
        return normalized
    return normalized + REPLAN_NOVELTY_GUIDANCE


def replan_obligation_id_from_packet(value: Any) -> str | None:
    packet: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    return normalize_todo_replan_obligation_id(packet.get("obligation_id"))


def todo_lifecycle_settlement_obligation(
    value: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Return the lifecycle-settlement subtype from a status payload or obligation."""

    raw_obligation = value.get("autonomous_replan_obligation")
    obligation: Mapping[str, Any] = (
        raw_obligation if isinstance(raw_obligation, Mapping) else value
    )
    if obligation.get("resolution_mode") != TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE:
        return None
    return obligation


def project_todo_lifecycle_settlement_reentry(
    payload: Mapping[str, Any],
    *,
    goal_id: str,
    lifecycle_actor_args: str,
    scoped_cli_args: str,
    runtime_root: str | None = None,
) -> dict[str, Any] | None:
    """Adapt a lifecycle obligation to the TS-owned reentry projection."""

    obligation = todo_lifecycle_settlement_obligation(payload)
    if obligation is None:
        return None
    raw_triggers = obligation.get("triggers")
    triggers = [
        {
            key: item.get(key)
            for key in ("kind", "todo_id", "completion_turn_key")
            if item.get(key) is not None
        }
        for item in (raw_triggers if isinstance(raw_triggers, list) else [])
        if isinstance(item, Mapping)
        and item.get("kind") == "completed_advancement_without_successor"
        and normalize_todo_id(item.get("todo_id"))
    ]
    return project_todo_lifecycle_reentry_effect(
        goal_id=goal_id,
        runtime_root=runtime_root,
        triggers=triggers,
        lifecycle_actor_args=shlex.split(lifecycle_actor_args),
        quota_scoped_args=shlex.split(scoped_cli_args),
    )


def build_autonomous_replan_cli_actions(
    payload: Mapping[str, Any],
    *,
    goal_id: str,
    settlement_args: str,
    scoped_cli_args: str,
    quota_spend_action: str,
    settlement_chain_ready: bool,
    command_prefix: str = "loopx",
    lifecycle_actor_args: str = "",
    runtime_root: str | None = None,
) -> list[str]:
    lifecycle_reentry = project_todo_lifecycle_settlement_reentry(
        payload,
        goal_id=goal_id,
        lifecycle_actor_args=lifecycle_actor_args,
        scoped_cli_args=scoped_cli_args,
        runtime_root=runtime_root,
    )
    if lifecycle_reentry is not None:
        return list(lifecycle_reentry["next_cli_actions"])
    raw_packet = payload.get("replan_action_packet")
    packet: Mapping[str, Any] = (
        raw_packet if isinstance(raw_packet, Mapping) else {}
    )
    raw_writeback_contract = packet.get("writeback_contract")
    writeback_contract: Mapping[str, Any] = (
        raw_writeback_contract
        if isinstance(raw_writeback_contract, Mapping)
        else {}
    )
    successor_command = str(
        writeback_contract.get("successor_command") or ""
    ).strip()
    if successor_command:
        return [
            "execute replan_action_packet.writeback_contract.successor_command",
            "on host_action=end_current_heartbeat: stop",
        ]
    raw_obligation = payload.get("autonomous_replan_obligation")
    obligation: Mapping[str, Any] = (
        raw_obligation if isinstance(raw_obligation, Mapping) else payload
    )
    semantic_delta_args = replan_writeback_requirements(obligation)["cli_semantic_args"]
    delivery_args = (
        "--delivery-batch-scale single_surface "
        "--delivery-outcome outcome_progress "
        if settlement_chain_ready
        else ""
    )
    cli_prefix = command_prefix.strip() or "loopx"
    refresh_action = (
        f"{cli_prefix} --format json refresh-state --goal-id {goal_id} "
        "--progress-scope agent_lane "
        "--classification bounded_replan_progress "
        f"{delivery_args}{semantic_delta_args}"
        f"{settlement_args}{scoped_cli_args}"
    )
    if not settlement_chain_ready:
        return [refresh_action]
    return [refresh_action, quota_spend_action]


def ensure_replan_novelty_policy(
    obligation: dict[str, Any],
) -> dict[str, Any]:
    """Upgrade any legacy obligation onto the policy-owned evidence path."""

    normalized = dict(obligation)
    if (
        normalized.get("resolution_mode")
        == TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE
    ):
        normalized["recommended_action"] = str(
            normalized.get("recommended_action")
            or "settle the exact completed Todo lifecycle"
        ).strip()
        normalized["replan_novelty_policy"] = {
            **build_replan_novelty_policy(),
            "writeback": "todo_lifecycle_or_typed_successor",
        }
    else:
        normalized["recommended_action"] = with_replan_novelty_guidance(
            str(normalized.get("recommended_action") or "run a bounded autonomous replan")
        )
        normalized["replan_novelty_policy"] = build_replan_novelty_policy()
    rearmed_after_obligation_id = normalize_todo_replan_obligation_id(
        normalized.get("rearmed_after_obligation_id")
    )
    if rearmed_after_obligation_id:
        normalized["rearmed_after_obligation_id"] = rearmed_after_obligation_id
    else:
        normalized.pop("rearmed_after_obligation_id", None)
    trigger_identity = [
        {
            key: (
                trigger.get("obligation_identity_revision", trigger.get(key))
                if key == "frontier_revision" else trigger.get(key)
            )
            for key in (
                "kind",
                "frontier_identity",
                "frontier_revision",
                "monitor_target_id",
                "progress_fingerprint",
                "agent_id",
                *(
                    ("latest_generated_at", "oldest_counted_generated_at")
                    if trigger.get("kind")
                    in {"periodic_review", "periodic_review_due"}
                    else ()
                ),
            )
            if trigger.get(key) is not None
        }
        for trigger in normalized.get("triggers") or []
        if isinstance(trigger, dict)
    ]
    identity_payload = {
        "schema_version": normalized.get("schema_version"),
        "agent_id": normalized.get("agent_id"),
        "frontier_identity": normalized.get("frontier_identity"),
        "rearmed_after_obligation_id": rearmed_after_obligation_id,
        "trigger_identity": trigger_identity,
    }
    normalized["obligation_id"] = "replan-" + hashlib.sha256(
        json.dumps(
            identity_payload,
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return normalized


# Unchanged-poll streak before a monitor-only lane is forced to replan. Kept
# deliberately above the 2-turn run-history stall threshold: quiet monitors
# legitimately wait several cadence cycles for external evidence, and forcing
# replan after two unchanged polls creates churn for slow external sources.
MONITOR_NO_CHANGE_STREAK_THRESHOLD = 5
def _single_public_agent_id(items: list[dict[str, Any]]) -> str | None:
    agent_ids = {
        str(item.get("agent_id") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("agent_id") or "").strip()
    }
    return next(iter(agent_ids)) if len(agent_ids) == 1 else None


def run_history_agent_id(run: dict[str, Any]) -> str | None:
    agent_id = str(run.get("agent_id") or "").strip()
    if agent_id:
        return agent_id
    monitor_target = run_history_monitor_target(run)
    if isinstance(monitor_target, dict):
        return str(monitor_target.get("agent_id") or "").strip() or None
    return None


def _latest_agent_run_history(
    latest_runs: list[dict[str, Any]] | None,
    *,
    neutral_classifications: set[str],
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Keep the newest attributable agent lane while preserving goal-level runs."""

    accountable_agent_id = str(agent_id or "").strip() or None
    if accountable_agent_id is None:
        accountable_agent_id = next(
            (
                run_history_agent_id(run)
                for run in latest_runs or []
                if isinstance(run, dict)
                and str(run.get("classification") or "").strip()
                not in neutral_classifications
                and run_history_agent_id(run)
            ),
            None,
        )
    if not accountable_agent_id:
        return [run for run in latest_runs or [] if isinstance(run, dict)]
    return [
        run
        for run in latest_runs or []
        if isinstance(run, dict)
        and run_history_agent_id(run) in {None, accountable_agent_id}
    ]


def run_history_monitor_target(run: dict[str, Any]) -> dict[str, Any] | None:
    target = run.get("monitor_target")
    if isinstance(target, dict):
        return target
    event = run.get("monitor_event")
    if isinstance(event, dict) and isinstance(event.get("monitor_target"), dict):
        return event.get("monitor_target")
    return None


def run_history_monitor_wait_already_acknowledged(
    latest_runs: list[dict[str, Any]] | None,
    *,
    signal_count: int,
    autonomous_replan_ack_recorded: AckRecorded,
    neutral_classifications: set[str],
) -> bool:
    """Return true when newer monitor-poll stalls already have a compact ack run behind them."""

    for run in (latest_runs or [])[signal_count:]:
        if not isinstance(run, dict):
            continue
        if autonomous_replan_ack_recorded(run):
            return True
        classification = str(run.get("classification") or "").strip()
        if not classification:
            continue
        if classification in neutral_classifications:
            continue
        if classification == "quota_monitor_poll":
            continue
        return False
    return False


def autonomous_replan_periodic_review_from_runs(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_todos: dict[str, Any] | None,
    autonomous_replan_ack_recorded: AckRecorded,
    neutral_classifications: set[str],
    periodic_run_threshold: int,
    build_autonomous_replan_obligation: Callable[..., dict[str, Any] | None],
) -> dict[str, Any] | None:
    durable_runs: list[dict[str, Any]] = []
    for run in latest_runs or []:
        if not isinstance(run, dict):
            continue
        if autonomous_replan_ack_recorded(run):
            break
        classification = str(run.get("classification") or "").strip()
        if not classification:
            continue
        if classification in neutral_classifications:
            continue
        durable_runs.append(run)
        if len(durable_runs) >= periodic_run_threshold:
            break

    if len(durable_runs) < periodic_run_threshold:
        return None

    evidence: list[dict[str, Any]] = [
        {
            "kind": "periodic_review_due",
            "section": "run_history",
            "text": (
                f"latest {len(durable_runs)} durable public run records since last autonomous "
                f"replan reached periodic review threshold {periodic_run_threshold}"
            ),
            "run_count": len(durable_runs),
            "threshold": periodic_run_threshold,
            "latest_generated_at": str(durable_runs[0].get("generated_at") or ""),
            "oldest_counted_generated_at": str(durable_runs[-1].get("generated_at") or ""),
            "agent_id": _single_public_agent_id(durable_runs),
        }
    ]
    return build_autonomous_replan_obligation(evidence, agent_todos=agent_todos)


def _monitor_no_change_evidence(
    agent_todos: dict[str, Any] | None,
    *,
    threshold: int,
    schema_version: str,
) -> dict[str, Any] | None:
    if not isinstance(agent_todos, dict):
        return None
    raw_monitors = agent_todos.get("monitor_open_items")
    monitors = [
        item
        for item in (raw_monitors if isinstance(raw_monitors, list) else [])
        if isinstance(item, dict)
    ]
    stalled: list[tuple[int, dict[str, Any]]] = []
    for item in monitors:
        try:
            no_change_count = int(str(item.get("consecutive_no_change") or "0"))
        except ValueError:
            continue
        if no_change_count >= threshold:
            stalled.append((no_change_count, item))
    if not stalled:
        return None

    stalled.sort(key=lambda pair: pair[0], reverse=True)
    no_change_count, monitor = stalled[0]
    agent_id = str(monitor.get("claimed_by") or "").strip() or None
    raw_advancements = agent_todos.get("executable_backlog_items")
    if not isinstance(raw_advancements, list):
        raw_advancements = agent_todos.get("items")
    for item in raw_advancements or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").strip().lower() != "open":
            continue
        if str(item.get("task_class") or "").strip() != "advancement_task":
            continue
        claimed_by = str(item.get("claimed_by") or "").strip() or None
        if not claimed_by or claimed_by == agent_id:
            return None

    monitor_target_id = str(
        monitor.get("target_key") or monitor.get("todo_id") or "monitor"
    ).strip()
    return {
        "kind": "monitor_no_change_streak",
        "schema_version": schema_version,
        "section": "agent_todos",
        "text": (
            f"monitor {monitor_target_id} recorded {no_change_count} "
            "consecutive unchanged polls without runnable advancement"
        ),
        "run_count": no_change_count,
        "threshold": threshold,
        "monitor_target_id": monitor_target_id,
        "agent_id": agent_id,
    }


def _future_due_blocking_monitor(
    agent_todos: dict[str, Any] | None,
    *,
    latest_generated_at: str,
    agent_id: str | None,
) -> dict[str, str] | None:
    if not isinstance(agent_todos, dict):
        return None
    observed_at = parse_timestamp(latest_generated_at)
    if observed_at is None:
        return None

    accountable_agent_id = normalize_todo_claimed_by(agent_id)
    if not accountable_agent_id:
        return None
    raw_monitors = agent_todos.get("monitor_open_items")
    monitor_items = raw_monitors if isinstance(raw_monitors, list) else []
    monitors_by_id: dict[str, dict[str, Any]] = {}
    for monitor in monitor_items:
        if not isinstance(monitor, dict):
            continue
        monitor_todo_id = normalize_todo_id(monitor.get("todo_id"))
        claimed_by = normalize_todo_claimed_by(monitor.get("claimed_by"))
        if not monitor_todo_id:
            continue
        if accountable_agent_id and claimed_by and claimed_by != accountable_agent_id:
            continue
        monitors_by_id[monitor_todo_id] = monitor

    blocking_monitor_ids: set[str] = set()
    resume_planning = project_todo_resume_planning(agent_todos)
    blocked_items = [
        *resume_planning["monitor_blocked_items"],
        *resume_planning["deferred_items"],
    ]
    for item in blocked_items:
        claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
        if accountable_agent_id and claimed_by and claimed_by != accountable_agent_id:
            continue
        raw_condition = item.get("resume_condition")
        condition = raw_condition if isinstance(raw_condition, dict) else {}
        monitor_todo_id = normalize_todo_id(
            item.get("blocking_monitor_todo_id")
            or condition.get("target_todo_id")
            or condition.get("target")
        )
        if monitor_todo_id in monitors_by_id:
            blocking_monitor_ids.add(monitor_todo_id)
        for successor_todo_id in normalize_todo_id_list(
            item.get("successor_todo_ids")
        ):
            if successor_todo_id in monitors_by_id:
                blocking_monitor_ids.add(successor_todo_id)
    if not blocking_monitor_ids:
        return None

    for monitor_todo_id in blocking_monitor_ids:
        monitor = monitors_by_id[monitor_todo_id]
        next_due_at = parse_timestamp(monitor.get("next_due_at"))
        if next_due_at is None or next_due_at <= observed_at:
            continue
        expires_at = parse_timestamp(monitor.get("expires_at"))
        if expires_at is not None and (
            expires_at <= observed_at or expires_at <= next_due_at
        ):
            continue
        return {
            "todo_id": monitor_todo_id,
            "next_due_at": str(monitor.get("next_due_at") or ""),
        }
    return None


def build_autonomous_replan_obligation(
    evidence: list[dict[str, Any]],
    *,
    agent_todos: dict[str, Any] | None,
    public_safe_compact_text: PublicSafeText,
    autonomous_replan_schema_version: str,
    autonomous_replan_stall_threshold: int,
    dead_monitor_repeat_threshold: int,
    dead_monitor_repeat_schema_version: str,
) -> dict[str, Any] | None:
    if not evidence:
        monitor_evidence = _monitor_no_change_evidence(
            agent_todos,
            threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD,
            schema_version=dead_monitor_repeat_schema_version,
        )
        if monitor_evidence:
            evidence = [monitor_evidence]
    if not evidence:
        return None

    dead_monitor_evidence = next(
        (
            item
            for item in evidence
            if item.get("kind") in {"dead_monitor_repeat", "monitor_no_change_streak"}
        ),
        None,
    )
    blocked_successor_evidence = next(
        (
            item
            for item in evidence
            if item.get("kind") == "blocked_successor_no_progress_repeat"
        ),
        None,
    )
    first_open: dict[str, Any] = {}
    if isinstance(agent_todos, dict):
        open_items = agent_todos.get("first_open_items")
        if isinstance(open_items, list) and open_items and isinstance(open_items[0], dict):
            first_open = open_items[0]

    evidence_has_agent_attribution = any("agent_id" in item for item in evidence)
    replan_agent_id = _single_public_agent_id(evidence)
    if replan_agent_id is None and not evidence_has_agent_attribution:
        replan_agent_id = str(first_open.get("claimed_by") or "").strip() or None

    todo_actions: list[dict[str, Any]] = []
    first_open_text = public_safe_compact_text(first_open.get("text"), limit=140)
    if first_open_text:
        action: dict[str, Any] = {
            "action": "split",
            "role": "agent",
            "text": first_open_text,
        }
        if first_open.get("priority"):
            action["priority"] = first_open.get("priority")
        todo_actions.append(action)
    if blocked_successor_evidence:
        todo_actions.append(
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": (
                    "discover and promote one safe in-scope evidence-backed successor; "
                    "otherwise record a new evidence-backed blocker or bounded terminal"
                ),
            }
        )
    elif dead_monitor_evidence:
        todo_actions.append(
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": (
                    "resolve the repeated monitor target with watch-lane expiry, "
                    "a concrete blocker, todo supersede, or successor runnable todo"
                ),
            }
        )
    else:
        todo_actions.append(
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": (
                    "write a compact replan record naming trigger, selected next slice, "
                    "validation command, and stop condition"
                ),
            }
        )
    if any(item.get("kind") in {"no_progress_streak", "repeated_action_loop"} for item in evidence):
        todo_actions.append(
            {
                "action": "retire",
                "role": "agent",
                "priority": "P2",
                "text": "retire or downgrade stale monitor-only next actions after the executable replan is selected",
            }
        )
    if any(item.get("kind") in {"periodic_review", "periodic_review_due"} for item in evidence):
        todo_actions.append(
            {
                "action": "ask_decision",
                "role": "user",
                "priority": "P2",
                "text": (
                    "ask the operator only if the review changes benchmark family, public claims, "
                    "resource budget, or protected scope"
                ),
            }
        )

    if blocked_successor_evidence:
        recommended_action = (
            "run a bounded autonomous replan for the exact blocked successor: "
            "promote one safe in-scope evidence-backed successor when available; "
            "otherwise record a new evidence-backed blocker or coverage-backed terminal"
        )
    elif dead_monitor_evidence:
        recommended_action = (
            "resolve a dead monitor loop with a new evidence-backed blocker, todo "
            "supersede, runnable successor, or coverage-backed terminal before another "
            "quiet monitor poll"
        )
    elif any(item.get("kind") in {"periodic_review", "periodic_review_due"} for item in evidence):
        recommended_action = (
            "run a bounded autonomous periodic review: keep, split, add, retire, or ask for "
            "a decision; then update todos and select the next validated slice"
        )
    else:
        recommended_action = (
            "run an autonomous replan after two consecutive stalled turns before another "
            "monitor-only or repeated action consumes the eligible turn"
        )

    extra_fields: dict[str, Any] = {}
    if (
        blocked_successor_evidence
        and blocked_successor_evidence.get("frontier_identity")
    ):
        extra_fields["frontier_identity"] = blocked_successor_evidence.get(
            "frontier_identity"
        )
    elif dead_monitor_evidence and dead_monitor_evidence.get("monitor_target_id"):
        extra_fields["frontier_identity"] = dead_monitor_evidence.get(
            "monitor_target_id"
        )
    typed_progress_evidence = next(
        (
            item
            for item in evidence
            if item.get("kind") == "typed_progress_repeat"
            and isinstance(item.get("progress_baseline"), dict)
        ),
        None,
    )
    if typed_progress_evidence:
        extra_fields["progress_baseline"] = typed_progress_evidence[
            "progress_baseline"
        ]
        extra_fields["frontier_identity"] = (
            "progress:"
            + str(typed_progress_evidence.get("progress_fingerprint") or "")
        )
    result = build_autonomous_replan_obligation_payload(
        schema_version=autonomous_replan_schema_version,
        stall_threshold=(
            int(dead_monitor_evidence.get("threshold") or dead_monitor_repeat_threshold)
            if dead_monitor_evidence
            else autonomous_replan_stall_threshold
        ),
        trigger_count=len(evidence),
        triggers=evidence,
        guidance_actions=(
            ["set_watch_expiry", "write_blocker", "supersede_monitor", "create_successor"]
            if dead_monitor_evidence
            else [
                "discover_safe_successor",
                "create_successor",
                "write_blocker",
                "record_coverage_terminal",
            ]
            if blocked_successor_evidence
            else ["keep", "split", "add", "retire", "ask_decision"]
        ),
        todo_actions=todo_actions[:3],
        stop_condition=(
            "stop if the replan requires private material, credentials, destructive git, "
            "production actions, or owner-only decisions"
        ),
        recommended_action=recommended_action,
        agent_id=replan_agent_id,
        extra_fields=extra_fields,
    )
    if dead_monitor_evidence:
        result["dead_monitor_detector"] = {
            "schema_version": dead_monitor_repeat_schema_version,
            "monitor_target_id": dead_monitor_evidence.get("monitor_target_id"),
            "run_count": dead_monitor_evidence.get("run_count"),
            "threshold": dead_monitor_evidence.get("threshold"),
            "required_resolution": [
                "watch_lane_expiry",
                "blocker",
                "todo_supersede",
                "successor_runnable_todo",
            ],
        }
    return result


def build_autonomous_replan_obligation_payload(
    *,
    schema_version: str,
    stall_threshold: int,
    trigger_count: int,
    triggers: list[dict[str, Any]],
    guidance_actions: list[str],
    todo_actions: list[dict[str, Any]],
    stop_condition: str,
    recommended_action: str,
    agent_id: str | None = None,
    include_agent_id: bool = False,
    rearmed_after_obligation_id: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "required": True,
        "stall_threshold": stall_threshold,
        "trigger_count": trigger_count,
        "triggers": triggers,
        "guidance_actions": guidance_actions,
        "todo_actions": todo_actions,
        "stop_condition": stop_condition,
        "recommended_action": recommended_action,
    }
    if include_agent_id or agent_id is not None:
        payload["agent_id"] = agent_id
    if extra_fields:
        payload.update(extra_fields)
    if rearmed_after_obligation_id is not None:
        payload["rearmed_after_obligation_id"] = rearmed_after_obligation_id
    return ensure_replan_novelty_policy(payload)


def autonomous_replan_obligation_from_runs(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_todos: dict[str, Any] | None,
    agent_id: str | None = None,
    autonomous_replan_ack_recorded: AckRecorded,
    neutral_classifications: set[str],
    build_autonomous_replan_obligation: Callable[..., dict[str, Any] | None],
    autonomous_replan_stall_threshold: int,
    dead_monitor_repeat_threshold: int,
    dead_monitor_repeat_schema_version: str,
    periodic_run_threshold: int,
) -> dict[str, Any] | None:
    scoped_latest_runs = _latest_agent_run_history(
        latest_runs,
        neutral_classifications=neutral_classifications,
        agent_id=agent_id,
    )

    def periodic_review() -> dict[str, Any] | None:
        return autonomous_replan_periodic_review_from_runs(
            scoped_latest_runs,
            agent_todos=agent_todos,
            autonomous_replan_ack_recorded=autonomous_replan_ack_recorded,
            neutral_classifications=neutral_classifications,
            periodic_run_threshold=periodic_run_threshold,
            build_autonomous_replan_obligation=build_autonomous_replan_obligation,
        )

    typed_repeat = typed_progress_repeat_trigger(
        scoped_latest_runs,
        agent_id=agent_id,
        threshold=autonomous_replan_stall_threshold,
    )
    if typed_repeat:
        return build_autonomous_replan_obligation(
            [typed_repeat],
            agent_todos=agent_todos,
        )

    # Monitor rows already carry a typed monitor target. Keep this explicit
    # state-machine input; do not infer monitor/stall state from prose fields.
    monitor_signals: list[dict[str, Any]] = []
    signal_scan_limit = max(
        autonomous_replan_stall_threshold,
        dead_monitor_repeat_threshold,
    )
    for run in scoped_latest_runs:
        if not isinstance(run, dict):
            continue
        if autonomous_replan_ack_recorded(run):
            break
        classification = str(run.get("classification") or "").strip()
        if classification in neutral_classifications:
            continue
        if classification != "quota_monitor_poll":
            break
        monitor_target = run_history_monitor_target(run)
        if not isinstance(monitor_target, dict):
            break
        monitor_target_id = str(monitor_target.get("target_id") or "").strip()
        monitor_mode = str(monitor_target.get("monitor_mode") or "").strip()
        if not monitor_target_id or not monitor_mode:
            break
        signal: dict[str, Any] = {
            "classification": classification,
            "generated_at": str(run.get("generated_at") or ""),
            "agent_id": run_history_agent_id(run),
            "monitor_target_id": monitor_target_id,
            "monitor_target": {
                key: monitor_target.get(key)
                for key in (
                    "schema_version",
                    "target_id",
                    "monitor_mode",
                    "effective_action",
                    "agent_id",
                    "frontier_identity",
                )
                if monitor_target.get(key)
            },
        }
        monitor_event = run.get("monitor_event")
        if not isinstance(monitor_event, dict):
            monitor_event = {}
        todo_id = str(run.get("todo_id") or monitor_event.get("todo_id") or "").strip()
        target_key = str(
            run.get("target_key") or monitor_event.get("target_key") or ""
        ).strip()
        if todo_id:
            signal["todo_id"] = todo_id
        if target_key:
            signal["target_key"] = target_key
        turn_instance_id = str(run.get("turn_instance_id") or "").strip()
        if turn_instance_id:
            signal["turn_instance_id"] = turn_instance_id
        monitor_signals.append(signal)
        if len(monitor_signals) >= signal_scan_limit:
            break

    if len(monitor_signals) < autonomous_replan_stall_threshold:
        return periodic_review()

    blocked_successor_signals = monitor_signals[:autonomous_replan_stall_threshold]
    blocked_successor_modes = {
        str((signal.get("monitor_target") or {}).get("monitor_mode") or "")
        for signal in blocked_successor_signals
    }
    if blocked_successor_modes == {
        "blocked_successor_wait_without_material_transition"
    }:
        signal_agent_id = _single_public_agent_id(blocked_successor_signals)
        if _future_due_blocking_monitor(
            agent_todos,
            latest_generated_at=str(monitor_signals[0].get("generated_at") or ""),
            agent_id=signal_agent_id or agent_id,
        ):
            return periodic_review()
        monitor_target_ids = {
            str(signal.get("monitor_target_id") or "")
            for signal in blocked_successor_signals
            if signal.get("monitor_target_id")
        }
        frontier_identities = {
            str((signal.get("monitor_target") or {}).get("frontier_identity") or "")
            for signal in blocked_successor_signals
            if (signal.get("monitor_target") or {}).get("frontier_identity")
        }
        if len(monitor_target_ids) != 1 or len(frontier_identities) != 1:
            return periodic_review()
        evidence = [
            {
                "kind": "blocked_successor_no_progress_repeat",
                "section": "run_history",
                "run_count": len(blocked_successor_signals),
                "threshold": autonomous_replan_stall_threshold,
                "monitor_target_id": next(iter(monitor_target_ids)),
                "frontier_identity": next(iter(frontier_identities)),
                "latest_generated_at": monitor_signals[0].get("generated_at"),
                "agent_id": _single_public_agent_id(blocked_successor_signals),
            }
        ]
        return build_autonomous_replan_obligation(evidence, agent_todos=agent_todos)

    executed_monitor_signals = [
        signal
        for signal in monitor_signals
        if (
            (signal.get("todo_id") or signal.get("target_key"))
            and str((signal.get("monitor_target") or {}).get("monitor_mode") or "")
            in {
                "due_monitor_observed_without_material_transition",
                "external_monitor_observed_without_material_transition",
            }
        )
    ]
    repeated_monitors = executed_monitor_signals[:dead_monitor_repeat_threshold]
    if len(repeated_monitors) < dead_monitor_repeat_threshold:
        return periodic_review()
    monitor_target_ids = {
        str(signal.get("monitor_target_id") or "")
        for signal in repeated_monitors
        if signal.get("monitor_target_id")
    }
    if len(monitor_target_ids) != 1:
        return periodic_review()
    evidence = [
        {
            "kind": "dead_monitor_repeat",
            "schema_version": dead_monitor_repeat_schema_version,
            "section": "run_history",
            "run_count": len(repeated_monitors),
            "threshold": dead_monitor_repeat_threshold,
            "monitor_target_id": next(iter(monitor_target_ids)),
            "latest_generated_at": repeated_monitors[0].get("generated_at"),
            "agent_id": _single_public_agent_id(repeated_monitors),
        }
    ]
    return build_autonomous_replan_obligation(evidence, agent_todos=agent_todos)
