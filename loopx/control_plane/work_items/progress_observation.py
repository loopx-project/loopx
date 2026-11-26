"""Host validation, digest, and semantic writeback adapters for progress observations."""

from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from ...turn_identity import normalize_turn_instance_id
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..goals.goal_vision_state import normalize_goal_vision_state
from ..todos.contract import (
    normalize_todo_task_domain,
    replan_successor_semantic_binding,
)
from .progress_result import (
    PROGRESS_OBSERVATION_SCHEMA_VERSION,
    ProgressResultClass,
    normalize_progress_identifier,
)
from .replan_semantics_codec import project_replan_semantics

Payload = dict[str, object]
PayloadView = Mapping[str, object]

REPLAN_ACTION_PACKET_SCHEMA_VERSION = "replan_action_packet_v0"
PROGRESS_REPEAT_TRIGGER_KIND = "typed_progress_repeat"
PROGRESS_REPEAT_THRESHOLD = 2
MAX_PROGRESS_EVIDENCE_IDS = 12

SEMANTIC_DIMENSIONS = ("surface_id", "hypothesis_id", "probe_kind")
TERMINAL_RESULT_CLASSES = {
    ProgressResultClass.BLOCKED.value,
    ProgressResultClass.EXPLORATION_EXHAUSTED.value,
    ProgressResultClass.NO_FOLLOWUP.value,
}
# Legacy ACK readback vocabulary; discharge authority lives in replan_semantics.ts.
FRESH_VISION_PATH_DISPOSITIONS = frozenset({"continue", "no_change", "replan"})
_BOUNDED_FRONTIER_KEYS: tuple[str, ...] = (
    "schema_version",
    "gap_id",
    "experiment_node_ref",
    "input_node_refs",
    "required_outcome",
    "input_observations",
    "frontier_revision",
)
_IDENTITY_FIELD_KEYS: tuple[str, ...] = (
    "work_item_id",
    *SEMANTIC_DIMENSIONS,
    "result_class",
    "blocker_id",
    "coverage_scope_id",
    "evidence_ids",
    "coverage_complete",
)


def _stable_id(value: object, *, field: str, required: bool = False) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if normalize_progress_identifier(normalized) is None:
        raise ValueError(f"{field} must be an opaque 1-128 character public-safe identifier")
    return normalized


def _stable_ids(values: Iterable[object] | None, *, field: str) -> list[str]:
    seen: set[str] = set()
    for value in values or ():
        item = _stable_id(value, field=field, required=True)
        if item:
            seen.add(item)
    if len(seen) > MAX_PROGRESS_EVIDENCE_IDS:
        raise ValueError(f"{field} accepts at most {MAX_PROGRESS_EVIDENCE_IDS} identifiers")
    return sorted(seen)


def _digest(value: PayloadView) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def normalize_progress_observation(
    value: PayloadView,
    *,
    work_item_id: str | None = None,
) -> Payload:
    """Validate one explicit progress observation without interpreting prose."""
    if not isinstance(value, Mapping):
        raise TypeError("progress observation must be an object")
    schema_version = str(value.get("schema_version") or PROGRESS_OBSERVATION_SCHEMA_VERSION).strip()
    if schema_version != PROGRESS_OBSERVATION_SCHEMA_VERSION:
        raise ValueError(f"progress observation must use {PROGRESS_OBSERVATION_SCHEMA_VERSION}")
    try:
        result_class = ProgressResultClass(str(value.get("result_class") or ""))
    except ValueError as exc:
        choices = ", ".join(item.value for item in ProgressResultClass)
        raise ValueError(f"result_class must be one of: {choices}") from exc

    observation: Payload = {
        "schema_version": PROGRESS_OBSERVATION_SCHEMA_VERSION,
        "result_class": result_class.value,
    }
    for field in SEMANTIC_DIMENSIONS:
        normalized = _stable_id(value.get(field), field=field)
        if normalized:
            observation[field] = normalized
    for field, candidate in (
        ("work_item_id", value.get("work_item_id") or work_item_id),
        ("blocker_id", value.get("blocker_id")),
        ("coverage_scope_id", value.get("coverage_scope_id")),
    ):
        normalized = _stable_id(candidate, field=field)
        if normalized:
            observation[field] = normalized
    evidence_ids = _stable_ids(cast(Iterable[object] | None, value.get("evidence_ids")), field="evidence_ids")
    if evidence_ids:
        observation["evidence_ids"] = evidence_ids
    coverage_complete = value.get("coverage_complete")
    if coverage_complete is not None:
        if not isinstance(coverage_complete, bool):
            raise ValueError("coverage_complete must be a boolean")
        observation["coverage_complete"] = coverage_complete

    identity_fields = {key: observation[key] for key in _IDENTITY_FIELD_KEYS if key in observation}
    if len(identity_fields) == 1:  # result_class alone is not attributable.
        raise ValueError(
            "progress observation requires at least one stable work, coverage, "
            "or evidence identifier"
        )
    observation["fingerprint"] = _digest(identity_fields)
    return observation


def progress_observation_from_run(run: PayloadView) -> Payload | None:
    value = run.get("progress_observation")
    if not isinstance(value, Mapping):
        return None
    try:
        return normalize_progress_observation(value)
    except (TypeError, ValueError):
        # Invalid historical rows are not silently upgraded into typed truth.
        return None


def _progress_turn_instance_id(run: PayloadView) -> str | None:
    """Return a trustworthy logical-turn id for retry de-duplication."""

    def normalize(value: object) -> str | None:
        try:
            return normalize_turn_instance_id(str(value) if value is not None else None)
        except ValueError:
            return None

    direct = normalize(run.get("turn_instance_id"))
    settlement_value = run.get("settlement_identity")
    settlement = settlement_value if isinstance(settlement_value, Mapping) else {}
    settled = normalize(settlement.get("turn_instance_id"))
    if direct and settled and direct != settled:
        return None
    return direct or settled or None


def typed_progress_repeat_trigger(
    newest_first_runs: Iterable[PayloadView],
    *,
    agent_id: str | None,
    threshold: int = PROGRESS_REPEAT_THRESHOLD,
) -> Payload | None:
    """Compatibility entrypoint for the shared TypeScript history window."""
    from .replan_history_codec import project_replan_history

    return cast(
        Payload | None,
        project_replan_history(
            newest_first_runs, operation="progress", agent_id=agent_id, stall_threshold=threshold
        ),
    )


def _normalized_window(window: Iterable[PayloadView] | None) -> list[Payload]:
    """Normalize the typed observations an obligation window already holds."""
    normalized: list[Payload] = []
    for item in window or ():
        if not isinstance(item, Mapping):
            continue
        try:
            normalized.append(normalize_progress_observation(item))
        except (KeyError, TypeError, ValueError):
            continue
    return normalized


def replan_writeback_requirements(obligation: PayloadView) -> Payload:
    """Adapt the shared typed discharge policy for CLI and host projection."""
    try:
        result = project_replan_semantics({"operation": "requirements", "obligation": dict(obligation)})
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript replan requirements must be an object")
    return dict(result)


def required_semantic_outcomes(obligation: PayloadView) -> list[str]:
    return list(cast(Iterable[str], replan_writeback_requirements(obligation)["required_any_of"]))


def replan_successor_transition_allowed(obligation: PayloadView) -> bool:
    """Ask the typed owner whether a Todo transition can settle this review."""
    try:
        result = project_replan_semantics({"operation": "transition_eligibility", "obligation": dict(obligation)})
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or not isinstance(result.get("allowed"), bool):
        raise RuntimeError("TypeScript transition eligibility shape mismatch")
    return cast(bool, result["allowed"])


def guarded_replan_transition_delta(
    *,
    guard_scoped: bool,
    selected_obligation_id: str | None,
    transition_candidates: Sequence[PayloadView],
    goal_id: str | None = None,
    agent_id: str | None = None,
    guard_recorded_at: str | None = None,
    guard_turn_instance_id: str | None = None,
) -> Payload | None:
    """Adapt revalidated canonical receipts to the TS-owned exact Turn gate."""
    try:
        result = project_replan_semantics({
            "operation": "turn_transition", "guard_scoped": guard_scoped,
            "selected_obligation_id": selected_obligation_id, "transition_candidates": transition_candidates,
            "goal_id": goal_id, "agent_id": agent_id,
            "guard_recorded_at": guard_recorded_at, "guard_turn_instance_id": guard_turn_instance_id,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or "semantic_delta" not in result:
        raise RuntimeError("TypeScript guarded replan transition shape mismatch")
    delta = result["semantic_delta"]
    if delta is not None and not isinstance(delta, Mapping):
        raise RuntimeError("TypeScript guarded replan delta must be an object or null")
    return dict(delta) if delta is not None else None


def replan_obligation_trigger_kinds(obligation: PayloadView) -> list[str]:
    """Return the typed trigger sources owned by one obligation generation."""
    triggers = obligation.get("triggers")
    if not isinstance(triggers, list):
        return []
    return list(dict.fromkeys(
        kind for trigger in triggers
        if isinstance(trigger, Mapping) and (kind := str(trigger.get("kind") or "").strip())
    ))


def replan_obligation_trigger_checkpoints(obligation: PayloadView) -> list[dict[str, str]]:
    """Bind an ACK to typed trigger revisions supplied by the obligation."""
    triggers = obligation.get("triggers")
    if not triggers:
        return []
    checkpoints = effect_runtime_result(
        "todo.frontier_revision.project",
        {"schema_version": "todo_frontier_revision_request_v0", "operation": "trigger_checkpoints", "triggers": triggers},
    )["trigger_checkpoints"]
    if not isinstance(checkpoints, list):
        raise TypeError("typed frontier checkpoint response must be a list")
    return cast(list[dict[str, str]], checkpoints)


def _validated_replan_context(obligation: PayloadView) -> Payload:
    from .replan_context_codec import validate_replan_context

    context = validate_replan_context(obligation.get("replan_context"))
    if context["obligation_id"] != obligation.get("obligation_id"):
        raise ValueError("replan context obligation identity mismatch")
    return cast(Payload, context)


def _obligation_baseline_and_window(obligation: PayloadView) -> tuple[PayloadView | None, list[object] | None]:
    raw_baseline = obligation.get("progress_baseline")
    baseline: PayloadView | None = cast(PayloadView, raw_baseline) if isinstance(raw_baseline, Mapping) else None
    raw_window = obligation.get("progress_window")
    window: list[object] | None = raw_window if isinstance(raw_window, list) else None
    if baseline is None or window is None:
        triggers = obligation.get("triggers")
        if isinstance(triggers, list):
            for trigger in triggers:
                if not isinstance(trigger, Mapping):
                    continue
                if baseline is None and isinstance(trigger.get("progress_baseline"), Mapping):
                    baseline = cast(PayloadView, trigger.get("progress_baseline"))
                if window is None and isinstance(trigger.get("progress_window"), list):
                    window = cast(list[object], trigger.get("progress_window"))
                if baseline is not None and window is not None:
                    break
    return baseline, window


def semantic_delta_from_writeback(
    *,
    obligation: PayloadView,
    progress_observation: PayloadView | None,
    agent_vision: PayloadView | None = None,
    history_runs: Iterable[PayloadView] | None = None,
    recovering_writeback_path: str | None = None,
) -> Payload:
    """Qualify concrete typed writeback evidence against one obligation.

    Legacy ACK and repair-delta claims never count on this write path. Only a
    typed observation grounded in current state, or a fresh evidence-linked
    vision path, can contribute outcomes.
    """
    # Consume an iterable once without reopening or modifying its authority store.
    runs: Sequence[PayloadView] | None = (
        None if history_runs is None
        else (history_runs if isinstance(history_runs, (list, tuple)) else list(history_runs))
    )
    baseline, window = _obligation_baseline_and_window(obligation)
    context = _validated_replan_context(obligation) if obligation.get("replan_context") is not None else None
    coverage = context["coverage_ledger"] if context is not None else None
    claimed: list[object] = list(window) if window is not None else []
    # During an exact supplement, the current observation is already in the displayed
    # ledger; the full index below distinguishes its source row from prior repeats.
    if isinstance(coverage, list) and recovering_writeback_path is None:
        claimed.extend(coverage)
    if runs is not None and context is not None:
        from .replan_context_codec import replan_evidence_rows

        novelty_runs: Sequence[PayloadView] = runs
        if recovering_writeback_path is not None:
            novelty_runs = [run for run in runs if run.get("json_path") != recovering_writeback_path]
            if len(novelty_runs) != len(runs) - 1:
                raise ValueError("the original blocked writeback is not unique in replan history")
        claimed.extend(
            row["progress_observation"]
            for row in replan_evidence_rows(
                novelty_runs, goal_id=cast(str, context["goal_id"]), agent_id=cast(str | None, context["agent_id"])
            )
            if row["progress_observation"] is not None
        )
    elif recovering_writeback_path is not None:
        raise ValueError("blocked writeback recovery requires the original replan context and full history")
    elif context is not None and context.get("coverage_truncated"):
        raise ValueError("replan writeback requires the complete evidence history when coverage is truncated")

    progress_source = (
        {
            "current": normalize_progress_observation(progress_observation),
            "baseline": normalize_progress_observation(baseline) if baseline is not None else None,
            "window": _normalized_window(cast(Iterable[PayloadView], claimed)),
        }
        if isinstance(progress_observation, Mapping)
        else None
    )
    vision = dict(agent_vision) if isinstance(agent_vision, Mapping) else {}
    if vision:
        vision["state"] = normalize_goal_vision_state(vision.get("state"))
    source_review = None
    stride = vision.get("stride_decision")
    review = stride.get("previous_review") if isinstance(stride, Mapping) else None
    if runs is not None and context is not None and context.get("decision_followthrough") and isinstance(review, Mapping):
        from .replan_context_codec import replay_stride_review_evidence

        source_review = replay_stride_review_evidence(context=context, review=review, runs=runs)
    try:
        result = project_replan_semantics({
            "operation": "qualify", "obligation": dict(obligation),
            "progress_source": progress_source, "agent_vision": vision,
            **({"stride_review_evidence": source_review} if source_review is not None else {}),
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    return {
        **result,
        "trigger_kinds": replan_obligation_trigger_kinds(obligation),
        "trigger_checkpoints": replan_obligation_trigger_checkpoints(obligation),
        "obligation_id": obligation.get("obligation_id"),
    }


def build_replan_context(
    obligation: PayloadView, *, goal_id: str, agent_id: str | None,
    newest_first_runs: Iterable[PayloadView], source_status: PayloadView | None = None,
    explore_context: PayloadView | None = None, goal_acceptance_contract: PayloadView | None = None,
    todo_items: Iterable[PayloadView] | None = None,
) -> Payload:
    from .replan_context_codec import project_replan_context

    raw_agent_id = agent_id or obligation.get("agent_id")
    resolved_agent = raw_agent_id if raw_agent_id is None or isinstance(raw_agent_id, str) else str(raw_agent_id)
    return cast(Payload, project_replan_context(
        goal_id=goal_id, agent_id=resolved_agent, runs=newest_first_runs,
        obligation=obligation, source_status=source_status,
        goal_acceptance_contract=goal_acceptance_contract, history_newest_first=True,
        explore_context=explore_context, todo_items=todo_items,
    ))


def _successor_writeback_contract(
    *, obligation_id: object, goal_id: str | None, agent_id: object,
    selected_gap: PayloadView, priority: str,
) -> Payload | None:
    raw_binding = selected_gap.get("successor_binding")
    raw_successor_binding: PayloadView = raw_binding if isinstance(raw_binding, Mapping) else {}
    successor_binding = replan_successor_semantic_binding(
        action_kind=raw_successor_binding.get("action_kind"),
        target_key=raw_successor_binding.get("target_key"),
        explore_result_node_refs=raw_successor_binding.get("explore_result_node_refs"),
    )
    successor_summary = str(selected_gap.get("successor_summary") or "").strip()[:240]
    if not (successor_summary and successor_binding):
        return None
    safe_goal_id = _stable_id(goal_id, field="goal_id") or "<goal-id>"
    safe_agent_id = _stable_id(agent_id, field="agent_id") or "<agent-id>"
    successor_text = shlex.quote(f"[{priority}] {successor_summary}")
    successor_ref_args = "".join(
        f" --explore-result-node-ref {shlex.quote(ref)}"
        for ref in successor_binding.get("explore_result_node_refs") or ()
    )
    target_key_arg = (
        " --target-key " + shlex.quote(str(successor_binding["target_key"]))
        if successor_binding.get("target_key")
        else ""
    )
    task_domain = normalize_todo_task_domain(raw_successor_binding.get("task_domain"))
    task_domain_arg = " --task-domain " + shlex.quote(task_domain) if task_domain else ""
    return {
        "successor_command": (
            "loopx todo add "
            f"--goal-id {safe_goal_id} --role agent --task-class advancement_task "
            f"--action-kind {shlex.quote(str(successor_binding['action_kind']))}"
            f"{task_domain_arg}{target_key_arg} --text {successor_text} "
            f"--claimed-by {safe_agent_id} --replan-obligation-id {obligation_id}{successor_ref_args}"
        ),
        "successor_host_action": "end_current_heartbeat",
    }


def build_replan_action_packet(
    obligation: PayloadView, *, goal_id: str | None = None,
    agent_id: str | None = None, bounded_research_frontier: PayloadView | None = None,
) -> Payload:
    settlement_packet = obligation.get("settlement_action_packet")
    if isinstance(settlement_packet, Mapping):
        return dict(settlement_packet)
    context = _validated_replan_context(obligation)
    todo_actions = obligation.get("todo_actions")
    todo_action: PayloadView = next((
        cast(PayloadView, item)
        for item in (todo_actions if isinstance(todo_actions, list) else ())
        if isinstance(item, Mapping) and item.get("action") == "add"
        and item.get("role") == "agent" and str(item.get("text") or "").strip()
    ), {})
    successor_priority = str(todo_action.get("priority") or "P1").strip()
    if successor_priority not in {"P0", "P1", "P2", "P3", "P4"}:
        successor_priority = "P1"
    raw_gap = bounded_research_frontier.get("selected_gap") if isinstance(bounded_research_frontier, Mapping) else None
    selected_gap: PayloadView | None = cast(PayloadView, raw_gap) if isinstance(raw_gap, Mapping) else None
    requirements = replan_writeback_requirements(obligation)
    writeback_contract = requirements["writeback_contract"]
    if selected_gap is not None:
        override = _successor_writeback_contract(
            obligation_id=obligation.get("obligation_id"), goal_id=goal_id,
            agent_id=agent_id or obligation.get("agent_id"),
            selected_gap=selected_gap, priority=successor_priority,
        )
        if override is not None:
            writeback_contract = override
    else:
        replan_successor_semantic_binding(action_kind=None, target_key=None, explore_result_node_refs=None)
    packet: Payload = {
        "schema_version": REPLAN_ACTION_PACKET_SCHEMA_VERSION,
        "decision": "replan_required",
        "obligation_id": obligation.get("obligation_id"),
        "uncovered_frontier": context["uncovered_frontier"],
        "required_outcome": "semantic_delta",
        "planning_guidance": requirements["planning_guidance"],
        "writeback_contract": writeback_contract,
        "allowed_terminal": [
            ProgressResultClass.EXPLORATION_EXHAUSTED.value,
            ProgressResultClass.BLOCKED.value,
            ProgressResultClass.NO_FOLLOWUP.value,
        ],
    }
    if "stride_control" in requirements:
        packet["stride_control"] = requirements["stride_control"]
    capability_guard = obligation.get("capability_guard")
    if isinstance(capability_guard, Mapping):
        packet["capability_guard"] = dict(capability_guard)
        packet["allowed_terminal"] = []
    if selected_gap is not None:
        packet["bounded_frontier"] = {
            key: selected_gap[key] for key in _BOUNDED_FRONTIER_KEYS if key in selected_gap
        }
    return packet
