"""Policy, identity, guidance, and CLI action helpers for autonomous replan."""

from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ..todos.contract import normalize_todo_id, normalize_todo_replan_obligation_id
from .external_progress_review import EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND
from .progress_observation import replan_writeback_requirements
from .replan_settlement import (
    project_todo_lifecycle_settlement_reentry as project_todo_lifecycle_reentry_effect,
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]
PublicSafeText = Callable[..., str | None]

MAX_AUTONOMOUS_REPLAN_TRIGGERS = 3
AUTONOMOUS_REPLAN_STALL_THRESHOLD = 2
AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID = "autonomous_replan_default_v0"
AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION = (
    "stop only the affected path when required access, destructive git, production actions, "
    "or owner-only decisions exceed existing user authority. Keep authorized private reads "
    "within their storage and publication boundaries; do not expose credentials or disclose "
    "private material beyond that authority. Continue independent authorized replan work "
    "while honoring explicit stops"
)
AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION = (
    "stop this replan path until a compatible reader validates its digest-bound "
    "review guard"
)
REPLAN_NOVELTY_POLICY_SCHEMA_VERSION = "replan_evidence_delivery_policy_v0"
TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE = "todo_lifecycle_settlement"
REPLAN_NOVELTY_GUIDANCE = (
    " Use the host-projected coverage ledger and produce a typed semantic delta; "
    "repeated observations cannot close replan."
)
# Unchanged-poll streak before a monitor-only lane is forced to replan. Kept
# deliberately above the 2-turn run-history stall threshold: quiet monitors
# legitimately wait several cadence cycles for external evidence, and forcing
# replan after two unchanged polls creates churn for slow external sources.
MONITOR_NO_CHANGE_STREAK_THRESHOLD = 5

_BASE_IDENTITY_KEYS: tuple[str, ...] = (
    "frontier_identity",
    "frontier_revision",
    "monitor_target_id",
    "progress_fingerprint",
    "agent_id",
)
_DEAD_MONITOR_KINDS = frozenset({"dead_monitor_repeat", "monitor_no_change_streak"})
_STALL_LOOP_KINDS = frozenset({"no_progress_streak", "repeated_action_loop"})
_PERIODIC_REVIEW_KINDS = frozenset({"periodic_review", "periodic_review_due"})
_LEGACY_REVISION_KINDS = frozenset({"goal_acceptance_stale", "goal_acceptance_unbound"})


@dataclass(slots=True)
class TriggerEvidenceSummary:
    """Single-pass summary across replan trigger evidence."""

    dead_monitor: PayloadView | None = None
    blocked_successor: PayloadView | None = None
    review: PayloadView | None = None
    typed_progress: PayloadView | None = None
    has_agent_attribution: bool = False
    has_stall_loop: bool = False
    has_periodic_review: bool = False
    agent_ids: set[str] = field(default_factory=set)


def summarize_trigger_evidence(evidence: Sequence[PayloadView]) -> TriggerEvidenceSummary:
    """Collect trigger categories, agent attribution, and review state in one pass."""

    summary = TriggerEvidenceSummary()
    for item in evidence:
        kind = item.get("kind")
        if summary.dead_monitor is None and kind in _DEAD_MONITOR_KINDS:
            summary.dead_monitor = item
        if summary.blocked_successor is None and kind == "blocked_successor_no_progress_repeat":
            summary.blocked_successor = item
        if summary.review is None and kind == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND:
            summary.review = item
        if (
            summary.typed_progress is None
            and kind == "typed_progress_repeat"
            and isinstance(item.get("progress_baseline"), Mapping)
        ):
            summary.typed_progress = item
        if "agent_id" in item:
            summary.has_agent_attribution = True
            agent_text = str(item.get("agent_id") or "").strip()
            if agent_text:
                summary.agent_ids.add(agent_text)
        if kind in _STALL_LOOP_KINDS:
            summary.has_stall_loop = True
        if kind in _PERIODIC_REVIEW_KINDS:
            summary.has_periodic_review = True
    return summary


def todo_actions_for_summary(
    summary: TriggerEvidenceSummary,
    *,
    first_open: PayloadView,
    public_safe_compact_text: PublicSafeText,
) -> list[Payload]:
    """Build capped todo action guidance from summarized trigger evidence."""

    todo_actions: list[Payload] = []
    first_open_text = public_safe_compact_text(first_open.get("text"), limit=140)
    if first_open_text:
        action: Payload = {"action": "split", "role": "agent", "text": first_open_text}
        if first_open.get("priority"):
            action["priority"] = first_open.get("priority")
        todo_actions.append(action)

    if summary.blocked_successor:
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
    elif summary.dead_monitor:
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
    elif summary.review:
        todo_actions.append(
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": (
                    "select a slice whose next change serves a named acceptance "
                    "criterion or adds evidence about it, or record with evidence why "
                    "the current slice is a necessary prerequisite or why the plan "
                    "stands; renamed, reordered or restated material and self-declared "
                    "advancement are not progress"
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

    if summary.has_stall_loop:
        todo_actions.append(
            {
                "action": "retire",
                "role": "agent",
                "priority": "P2",
                "text": (
                    "retire or downgrade stale monitor-only next actions after the "
                    "executable replan is selected"
                ),
            }
        )
    if summary.has_periodic_review:
        todo_actions.append(
            {
                "action": "ask_decision",
                "role": "user",
                "priority": "P2",
                "text": (
                    "ask the operator only if the review changes authorized goal scope, "
                    "public claims, resource budget, or protected scope"
                ),
            }
        )
    return todo_actions[:3]


def recommended_action_for_summary(summary: TriggerEvidenceSummary) -> str:
    """Resolve the primary recommended action from summarized trigger evidence."""

    if summary.blocked_successor:
        return (
            "run a bounded autonomous replan for the exact blocked successor: "
            "promote one safe in-scope evidence-backed successor when available; "
            "otherwise record a new evidence-backed blocker or coverage-backed terminal"
        )
    if summary.dead_monitor:
        return (
            "resolve a dead monitor loop with a new evidence-backed blocker, todo "
            "supersede, runnable successor, or coverage-backed terminal before another "
            "quiet monitor poll"
        )
    if summary.review:
        raw_count = summary.review.get("run_count")
        run_count = (
            int(raw_count) if isinstance(raw_count, (int, float, str)) and raw_count else 0
        )
        return (
            "run a bounded autonomous replan: the last "
            f"{run_count} observed changes were judged "
            "not to serve an acceptance criterion or add evidence about one; name the "
            "criterion the next slice serves and how it will be validated, or keep the "
            "plan with a typed observation that carries new evidence"
        )
    if summary.has_periodic_review:
        return (
            "run a bounded autonomous periodic review: keep, split, add, retire, or ask for "
            "a decision; then update todos and select the next validated slice"
        )
    return (
        "run an autonomous replan after two consecutive stalled turns before another "
        "monitor-only or repeated action consumes the eligible turn"
    )


def extra_fields_for_summary(summary: TriggerEvidenceSummary) -> Payload:
    """Build optional frontier and review baseline fields from summarized triggers."""

    extra_fields: Payload = {}
    if summary.blocked_successor and summary.blocked_successor.get("frontier_identity"):
        extra_fields["frontier_identity"] = summary.blocked_successor.get("frontier_identity")
    elif summary.dead_monitor and summary.dead_monitor.get("monitor_target_id"):
        extra_fields["frontier_identity"] = summary.dead_monitor.get("monitor_target_id")
    if summary.typed_progress:
        extra_fields["progress_baseline"] = summary.typed_progress["progress_baseline"]
        extra_fields["frontier_identity"] = (
            "progress:" + str(summary.typed_progress.get("progress_fingerprint") or "")
        )
    if summary.review:
        if summary.review.get("frontier_identity"):
            extra_fields["frontier_identity"] = summary.review["frontier_identity"]
        # The evaluated window's typed observation is the discharge baseline:
        # the writeback semantics accept only a genuinely new surface,
        # hypothesis, probe family, blocker or terminal coverage against it.
        if isinstance(summary.review.get("progress_baseline"), Mapping):
            extra_fields["progress_baseline"] = summary.review["progress_baseline"]
        # Every typed claim made while the obligation formed travels with it, so
        # the outcome owner can refuse a replay of any of them, not only the newest.
        raw_window = summary.review.get("progress_window")
        if isinstance(raw_window, list):
            extra_fields["progress_window"] = list(raw_window)
        raw_ids = summary.review.get("evidence_ids")
        extra_fields["external_progress_review"] = {
            "schema_version": summary.review.get("schema_version"),
            "signal": summary.review.get("signal"),
            "run_count": summary.review.get("run_count"),
            "threshold": summary.review.get("threshold"),
            "evidence_ids": list(raw_ids) if isinstance(raw_ids, list) else [],
            "contract_revision": summary.review.get("contract_revision"),
            "consecutive_drift": summary.review.get("consecutive_drift"),
            "unevaluated_transitions": summary.review.get("unevaluated_transitions"),
            "model_authority": "none",
            "effect": "required_obligation_under_goal_policy",
        }
    return extra_fields


def build_replan_novelty_policy() -> dict[str, str]:
    """Bind replan evidence delivery to the host-projected ledger."""

    return {
        "schema_version": REPLAN_NOVELTY_POLICY_SCHEMA_VERSION,
        "evidence_source": "compact_run_history",
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


def replan_obligation_id_from_packet(value: object) -> str | None:
    packet: PayloadView = value if isinstance(value, Mapping) else {}
    return normalize_todo_replan_obligation_id(packet.get("obligation_id"))


def todo_lifecycle_settlement_obligation(value: PayloadView) -> PayloadView | None:
    """Return the lifecycle-settlement subtype from a status payload or obligation."""

    raw_obligation = value.get("autonomous_replan_obligation")
    obligation: PayloadView = raw_obligation if isinstance(raw_obligation, Mapping) else value
    if obligation.get("resolution_mode") != TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE:
        return None
    return obligation


def project_todo_lifecycle_settlement_reentry(
    payload: PayloadView,
    *,
    goal_id: str,
    lifecycle_actor_args: str,
    scoped_cli_args: str,
    runtime_root: str | None = None,
    quota_guard_action: str | None = None,
) -> Payload | None:
    """Adapt a lifecycle obligation to the TS-owned reentry projection."""

    obligation = todo_lifecycle_settlement_obligation(payload)
    if obligation is None:
        return None
    raw_triggers = obligation.get("triggers")
    triggers: list[Payload] = [
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
    return dict(
        project_todo_lifecycle_reentry_effect(
            goal_id=goal_id,
            runtime_root=runtime_root,
            triggers=triggers,
            lifecycle_actor_args=shlex.split(lifecycle_actor_args),
            quota_scoped_args=shlex.split(scoped_cli_args),
            quota_guard_argv=shlex.split(quota_guard_action) if quota_guard_action else None,
        )
    )


def build_autonomous_replan_cli_actions(
    payload: PayloadView,
    *,
    goal_id: str,
    settlement_args: str,
    scoped_cli_args: str,
    quota_spend_action: str,
    settlement_chain_ready: bool,
    command_prefix: str = "loopx",
    lifecycle_actor_args: str = "",
    runtime_root: str | None = None,
    successor_closeout_guard: str | None = None,
    quota_guard_action: str | None = None,
) -> list[str]:
    lifecycle_reentry = project_todo_lifecycle_settlement_reentry(
        payload,
        goal_id=goal_id,
        lifecycle_actor_args=lifecycle_actor_args,
        scoped_cli_args=scoped_cli_args,
        runtime_root=runtime_root,
        quota_guard_action=quota_guard_action,
    )
    if lifecycle_reentry is not None:
        raw_actions = lifecycle_reentry.get("next_cli_actions")
        return [str(action) for action in raw_actions] if isinstance(raw_actions, list) else []
    raw_packet = payload.get("replan_action_packet")
    packet: PayloadView = raw_packet if isinstance(raw_packet, Mapping) else {}
    raw_writeback = packet.get("writeback_contract")
    writeback_contract: PayloadView = raw_writeback if isinstance(raw_writeback, Mapping) else {}
    successor_command = str(writeback_contract.get("successor_command") or "").strip()
    if successor_command:
        if settlement_chain_ready and successor_closeout_guard:
            return [
                "execute replan_action_packet.writeback_contract.successor_command",
                successor_closeout_guard,
                "finish only the original Turn's returned settlement actions before ending the heartbeat; "
                "do not execute or select its successor",
            ]
        return [
            "execute replan_action_packet.writeback_contract.successor_command",
            "on host_action=end_current_heartbeat: stop",
        ]
    raw_obligation = payload.get("autonomous_replan_obligation")
    obligation: PayloadView = raw_obligation if isinstance(raw_obligation, Mapping) else payload
    preferred_input = writeback_contract.get("preferred_input")
    if preferred_input == "verified_replan_writeback":
        return [quota_spend_action] if settlement_chain_ready else []
    semantic_delta_args = (
        ""
        if preferred_input == "exact_historical_ack"
        else str(
            writeback_contract.get("cli_semantic_args")
            or replan_writeback_requirements(obligation)["cli_semantic_args"]
        )
    )
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
        "--delivery-boundary semantic_closeout "
        f"{delivery_args}{semantic_delta_args}"
        f"{settlement_args}{scoped_cli_args}"
    )
    return [refresh_action, quota_spend_action] if settlement_chain_ready else [refresh_action]


def _obligation_digest(identity_payload: PayloadView) -> str:
    encoded = json.dumps(
        identity_payload,
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return "replan-" + hashlib.sha256(encoded).hexdigest()[:16]


def _extract_trigger_identities(
    triggers: object,
) -> tuple[list[Payload], dict[tuple[object, object], str]]:
    """Build canonical trigger identities and legacy revisions in one pass."""

    trigger_identity: list[Payload] = []
    legacy_revisions: dict[tuple[object, object], str] = {}
    if not isinstance(triggers, Sequence) or isinstance(triggers, (str, bytes)):
        return trigger_identity, legacy_revisions
    for trigger in triggers:
        if not isinstance(trigger, Mapping):
            continue
        kind = trigger.get("kind")
        keys = _BASE_IDENTITY_KEYS
        if kind == "completed_advancement_without_successor":
            keys = (*keys, "todo_id", "completion_turn_key")
        elif kind in _PERIODIC_REVIEW_KINDS:
            keys = (*keys, "latest_generated_at", "oldest_counted_generated_at")
        entry: Payload = {"kind": kind} if kind is not None else {}
        for key in keys:
            val = trigger.get(key)
            if val is not None:
                entry[key] = (
                    trigger.get("obligation_identity_revision", val)
                    if key == "frontier_revision"
                    else val
                )
        trigger_identity.append(entry)
        legacy_rev = trigger.get("legacy_frontier_revision")
        if kind in _LEGACY_REVISION_KINDS and isinstance(legacy_rev, str):
            legacy_revisions[(kind, trigger.get("frontier_revision"))] = legacy_rev
    return trigger_identity, legacy_revisions


def ensure_replan_novelty_policy(obligation: PayloadView) -> Payload:
    """Upgrade any legacy obligation onto the policy-owned evidence path."""

    normalized: Payload = dict(obligation)
    policy = build_replan_novelty_policy()
    if normalized.get("resolution_mode") == TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE:
        normalized["recommended_action"] = str(
            normalized.get("recommended_action") or "settle the exact completed Todo lifecycle"
        ).strip()
        policy["writeback"] = "todo_lifecycle_or_typed_successor"
    else:
        normalized["recommended_action"] = with_replan_novelty_guidance(
            str(normalized.get("recommended_action") or "run a bounded autonomous replan")
        )
    normalized["replan_novelty_policy"] = policy
    rearmed_after = normalize_todo_replan_obligation_id(
        normalized.get("rearmed_after_obligation_id")
    )
    if rearmed_after:
        normalized["rearmed_after_obligation_id"] = rearmed_after
    else:
        normalized.pop("rearmed_after_obligation_id", None)

    trigger_identity, legacy_revisions = _extract_trigger_identities(normalized.get("triggers"))
    identity_payload: Payload = {
        "schema_version": normalized.get("schema_version"),
        "agent_id": normalized.get("agent_id"),
        "frontier_identity": normalized.get("frontier_identity"),
        "rearmed_after_obligation_id": rearmed_after,
        "trigger_identity": trigger_identity,
    }
    normalized["obligation_id"] = _obligation_digest(identity_payload)
    # The pre-stable acceptance checkpoint included the current Todo timestamp.
    # Derive its exact original obligation from the same canonical trigger set.
    # This preserves an already-admitted Turn only while its owner/source still
    # matches; it does not mint an alternative identity for a fresh admission.
    if legacy_revisions:
        legacy_identity = [
            {
                **entry,
                "frontier_revision": legacy_revisions.get(
                    (entry.get("kind"), entry.get("frontier_revision")),
                    entry.get("frontier_revision"),
                ),
            }
            if "frontier_revision" in entry
            else entry
            for entry in trigger_identity
        ]
        normalized["legacy_obligation_id"] = _obligation_digest(
            {**identity_payload, "trigger_identity": legacy_identity}
        )
    else:
        normalized.pop("legacy_obligation_id", None)
    return normalized
