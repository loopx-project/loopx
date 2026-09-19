from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Iterable, Mapping
from typing import Any

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

REPLAN_CONTEXT_SCHEMA_VERSION = "replan_context_v0"
REPLAN_CONTEXT_RECEIPT_SCHEMA_VERSION = "replan_context_delivery_receipt_v0"
REPLAN_ACTION_PACKET_SCHEMA_VERSION = "replan_action_packet_v0"
PROGRESS_REPEAT_TRIGGER_KIND = "typed_progress_repeat"
PROGRESS_REPEAT_THRESHOLD = 2
MAX_PROGRESS_EVIDENCE_IDS = 12
MAX_COVERAGE_LEDGER_ITEMS = 6

SEMANTIC_DIMENSIONS = (
    "surface_id",
    "hypothesis_id",
    "probe_kind",
)
TERMINAL_RESULT_CLASSES = {
    ProgressResultClass.BLOCKED.value,
    ProgressResultClass.EXPLORATION_EXHAUSTED.value,
    ProgressResultClass.NO_FOLLOWUP.value,
}
# Legacy ACK readback vocabulary; discharge authority lives in replan_semantics.ts.
FRESH_VISION_PATH_DISPOSITIONS = frozenset(
    {"continue", "no_change", "replan"}
)


def _stable_id(value: Any, *, field: str, required: bool = False) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if normalize_progress_identifier(normalized) is None:
        raise ValueError(
            f"{field} must be an opaque 1-128 character public-safe identifier"
        )
    return normalized


def _stable_ids(values: Iterable[Any] | None, *, field: str) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        item = _stable_id(value, field=field, required=True)
        if item and item not in normalized:
            normalized.append(item)
    if len(normalized) > MAX_PROGRESS_EVIDENCE_IDS:
        raise ValueError(
            f"{field} accepts at most {MAX_PROGRESS_EVIDENCE_IDS} identifiers"
        )
    return sorted(normalized)


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]


def normalize_progress_observation(
    value: Mapping[str, Any],
    *,
    work_item_id: str | None = None,
) -> dict[str, Any]:
    """Validate one explicit progress observation without interpreting prose."""

    if not isinstance(value, Mapping):
        raise TypeError("progress observation must be an object")
    schema_version = str(
        value.get("schema_version") or PROGRESS_OBSERVATION_SCHEMA_VERSION
    ).strip()
    if schema_version != PROGRESS_OBSERVATION_SCHEMA_VERSION:
        raise ValueError(
            "progress observation must use "
            f"{PROGRESS_OBSERVATION_SCHEMA_VERSION}"
        )
    try:
        result_class = ProgressResultClass(str(value.get("result_class") or ""))
    except ValueError as exc:
        choices = ", ".join(item.value for item in ProgressResultClass)
        raise ValueError(f"result_class must be one of: {choices}") from exc

    observation: dict[str, Any] = {
        "schema_version": PROGRESS_OBSERVATION_SCHEMA_VERSION,
        "result_class": result_class.value,
    }
    for field in SEMANTIC_DIMENSIONS:
        normalized = _stable_id(value.get(field), field=field)
        if normalized:
            observation[field] = normalized
    normalized_work_item = _stable_id(
        value.get("work_item_id") or work_item_id,
        field="work_item_id",
    )
    if normalized_work_item:
        observation["work_item_id"] = normalized_work_item
    blocker_id = _stable_id(value.get("blocker_id"), field="blocker_id")
    if blocker_id:
        observation["blocker_id"] = blocker_id
    coverage_scope_id = _stable_id(
        value.get("coverage_scope_id"),
        field="coverage_scope_id",
    )
    if coverage_scope_id:
        observation["coverage_scope_id"] = coverage_scope_id
    evidence_ids = _stable_ids(value.get("evidence_ids"), field="evidence_ids")
    if evidence_ids:
        observation["evidence_ids"] = evidence_ids
    if value.get("coverage_complete") is not None:
        if not isinstance(value.get("coverage_complete"), bool):
            raise ValueError("coverage_complete must be a boolean")
        observation["coverage_complete"] = value.get("coverage_complete")

    identity_fields = {
        key: observation[key]
        for key in (
            "work_item_id",
            *SEMANTIC_DIMENSIONS,
            "result_class",
            "blocker_id",
            "coverage_scope_id",
            "evidence_ids",
            "coverage_complete",
        )
        if key in observation
    }
    if len(identity_fields) == 1:  # result_class alone is not attributable.
        raise ValueError(
            "progress observation requires at least one stable work, coverage, "
            "or evidence identifier"
        )
    observation["fingerprint"] = _digest(identity_fields)
    return observation


def progress_observation_from_run(run: Mapping[str, Any]) -> dict[str, Any] | None:
    value = run.get("progress_observation")
    if not isinstance(value, Mapping):
        return None
    try:
        return normalize_progress_observation(value)
    except (TypeError, ValueError):
        # Invalid historical rows are not silently upgraded into typed truth.
        return None


def _progress_turn_instance_id(run: Mapping[str, Any]) -> str | None:
    """Return a trustworthy logical-turn id for retry de-duplication."""

    def normalize(value: Any) -> str | None:
        try:
            return normalize_turn_instance_id(
                str(value) if value is not None else None
            )
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
    newest_first_runs: Iterable[Mapping[str, Any]],
    *,
    agent_id: str | None,
    threshold: int = PROGRESS_REPEAT_THRESHOLD,
) -> dict[str, Any] | None:
    """Return a repeat trigger only for consecutive equivalent typed rows."""

    required_count = max(2, int(threshold))
    normalized_agent_id = str(agent_id or "").strip()
    observations: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    observed_turn_instance_ids: set[str] = set()
    for run in newest_first_runs:
        run_agent_id = str(run.get("agent_id") or "").strip()
        if normalized_agent_id and run_agent_id not in {"", normalized_agent_id}:
            continue
        turn_instance_id = _progress_turn_instance_id(run)
        if turn_instance_id and turn_instance_id in observed_turn_instance_ids:
            continue
        observation = progress_observation_from_run(run)
        if observation is None:
            if observations:
                break
            continue
        observations.append((run, observation))
        if turn_instance_id:
            observed_turn_instance_ids.add(turn_instance_id)
        if len(observations) >= required_count:
            break
    if len(observations) < required_count:
        return None
    fingerprints = {item[1]["fingerprint"] for item in observations}
    if len(fingerprints) != 1:
        return None
    result_class = observations[0][1]["result_class"]
    if result_class not in {
        ProgressResultClass.UNCHANGED.value,
        ProgressResultClass.BLOCKED.value,
    }:
        return None
    baseline = observations[0][1]
    return {
        "kind": PROGRESS_REPEAT_TRIGGER_KIND,
        "schema_version": PROGRESS_OBSERVATION_SCHEMA_VERSION,
        "agent_id": normalized_agent_id or None,
        "run_count": required_count,
        "threshold": required_count,
        "progress_fingerprint": baseline["fingerprint"],
        "progress_baseline": baseline,
        "latest_generated_at": str(observations[0][0].get("generated_at") or ""),
        "oldest_counted_generated_at": str(
            observations[-1][0].get("generated_at") or ""
        ),
    }


def _has_new_terminal_coverage(
    current: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
) -> bool:
    """Compare terminal coverage semantics without treating proof churn as novelty."""

    if prior is None:
        return True
    result_class = current["result_class"]
    if result_class != prior.get("result_class"):
        return True
    if current.get("coverage_scope_id") != prior.get("coverage_scope_id"):
        return True
    if result_class == ProgressResultClass.EXPLORATION_EXHAUSTED.value:
        return current.get("coverage_complete") != prior.get("coverage_complete")
    return False


def semantic_progress_delta(
    observation: Mapping[str, Any] | None,
    *,
    baseline: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Qualify a typed observation as a replan-closing semantic delta."""

    if not isinstance(observation, Mapping):
        return {"accepted": False, "reason": "typed progress observation missing"}
    current = normalize_progress_observation(observation)
    prior = (
        normalize_progress_observation(baseline)
        if isinstance(baseline, Mapping)
        else None
    )
    result_class = current["result_class"]
    delta_kinds: list[str] = []
    if result_class == ProgressResultClass.ADVANCED.value:
        dimension_delta_names = {
            "surface_id": "new_surface",
            "hypothesis_id": "new_hypothesis",
            "probe_kind": "new_probe_family",
        }
        if current.get("evidence_ids"):
            for field, delta_kind in dimension_delta_names.items():
                value = current.get(field)
                if value and (prior is None or value != prior.get(field)):
                    delta_kinds.append(delta_kind)
    elif result_class == ProgressResultClass.BLOCKED.value:
        blocker_id = current.get("blocker_id")
        if (
            blocker_id
            and current.get("evidence_ids")
            and (prior is None or blocker_id != prior.get("blocker_id"))
        ):
            delta_kinds.append("new_concrete_blocker")
    elif result_class == ProgressResultClass.EXPLORATION_EXHAUSTED.value:
        if (
            current.get("coverage_complete") is True
            and current.get("coverage_scope_id")
            and current.get("evidence_ids")
            and _has_new_terminal_coverage(current, prior)
        ):
            delta_kinds.append("coverage_backed_exploration_exhausted")
    elif result_class == ProgressResultClass.NO_FOLLOWUP.value:
        if (
            current.get("coverage_scope_id")
            and current.get("evidence_ids")
            and _has_new_terminal_coverage(current, prior)
        ):
            delta_kinds.append("coverage_backed_no_followup")
    return {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": bool(delta_kinds),
        "delta_kinds": delta_kinds,
        "observation_fingerprint": current["fingerprint"],
        "baseline_fingerprint": prior.get("fingerprint") if prior else None,
        "reason": (
            "typed observation changes the replan frontier"
            if delta_kinds
            else "typed observation does not change an accepted semantic dimension"
        ),
    }


def replan_writeback_requirements(
    obligation: Mapping[str, Any],
) -> dict[str, Any]:
    """Adapt the shared typed discharge policy for CLI and host projection."""
    try:
        result = effect_runtime_result("work_item.replan_semantics.project", {
            "operation": "requirements", "obligation": dict(obligation),
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript replan requirements must be an object")
    return dict(result)


def required_semantic_outcomes(obligation: Mapping[str, Any]) -> list[str]:
    return list(replan_writeback_requirements(obligation)["required_any_of"])


def replan_obligation_trigger_kinds(
    obligation: Mapping[str, Any],
) -> list[str]:
    """Return the typed trigger sources owned by one obligation generation."""

    return list(
        dict.fromkeys(
            str(trigger.get("kind") or "").strip()
            for trigger in (obligation.get("triggers") or [])
            if isinstance(trigger, Mapping)
            and str(trigger.get("kind") or "").strip()
        )
    )


def replan_obligation_trigger_checkpoints(
    obligation: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Bind an ACK to typed trigger revisions supplied by the obligation."""

    triggers = obligation.get("triggers")
    if not triggers:
        return []
    checkpoints = effect_runtime_result("todo.frontier_revision.project", {
        "schema_version": "todo_frontier_revision_request_v0",
        "operation": "trigger_checkpoints", "triggers": triggers,
    })["trigger_checkpoints"]
    if not isinstance(checkpoints, list):
        raise TypeError("typed frontier checkpoint response must be a list")
    return checkpoints


def semantic_delta_from_writeback(
    *,
    obligation: Mapping[str, Any],
    progress_observation: Mapping[str, Any] | None,
    agent_vision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Qualify concrete typed writeback evidence against one obligation.

    Legacy ACK and repair-delta claims never count on this write path. Only a
    typed observation grounded in current state, or a fresh evidence-linked
    vision path, can contribute outcomes.
    """

    baseline = obligation.get("progress_baseline")
    if not isinstance(baseline, Mapping):
        baseline = next(
            (
                trigger.get("progress_baseline")
                for trigger in (obligation.get("triggers") or [])
                if isinstance(trigger, Mapping)
                and isinstance(trigger.get("progress_baseline"), Mapping)
            ),
            None,
        )
    observation_delta = semantic_progress_delta(
        progress_observation,
        baseline=baseline if isinstance(baseline, Mapping) else None,
    )
    vision = dict(agent_vision) if isinstance(agent_vision, Mapping) else {}
    if vision:
        vision["state"] = normalize_goal_vision_state(vision.get("state"))
    try:
        result = effect_runtime_result("work_item.replan_semantics.project", {
            "operation": "qualify", "obligation": dict(obligation),
            "observation_delta": observation_delta, "agent_vision": vision,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    return {
        **result,
        "trigger_kinds": replan_obligation_trigger_kinds(obligation),
        "trigger_checkpoints": replan_obligation_trigger_checkpoints(obligation),
        "obligation_id": obligation.get("obligation_id"),
    }


def _latest_typed_observations(
    newest_first_runs: Iterable[Mapping[str, Any]],
    *,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    normalized_agent_id = str(agent_id or "").strip()
    observations: list[dict[str, Any]] = []
    for run in newest_first_runs:
        run_agent_id = str(run.get("agent_id") or "").strip()
        if normalized_agent_id and run_agent_id not in {"", normalized_agent_id}:
            continue
        observation = progress_observation_from_run(run)
        if observation is None:
            continue
        observations.append(
            {
                **observation,
                "generated_at": str(run.get("generated_at") or ""),
            }
        )
        if len(observations) >= MAX_COVERAGE_LEDGER_ITEMS:
            break
    return observations


def build_replan_context(
    obligation: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    newest_first_runs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project compact evidence context into the action packet on the host."""

    obligation_id = _stable_id(
        obligation.get("obligation_id"),
        field="obligation_id",
        required=True,
    )
    coverage_ledger = _latest_typed_observations(
        newest_first_runs,
        agent_id=agent_id,
    )
    baseline = obligation.get("progress_baseline")
    if not isinstance(baseline, Mapping):
        baseline = next(
            (
                trigger.get("progress_baseline")
                for trigger in obligation.get("triggers") or []
                if isinstance(trigger, Mapping)
                and isinstance(trigger.get("progress_baseline"), Mapping)
            ),
            None,
        )
    uncovered_frontier = {
        "baseline": dict(baseline) if isinstance(baseline, Mapping) else None,
        "required_any_of": required_semantic_outcomes(obligation),
    }
    context_identity = {
        "goal_id": goal_id,
        "agent_id": str(agent_id or "").strip() or None,
        "obligation_id": obligation_id,
        "coverage_fingerprints": [
            item["fingerprint"] for item in coverage_ledger
        ],
        "uncovered_frontier": uncovered_frontier,
    }
    context_id = "replan-context-" + _digest(context_identity)
    return {
        "schema_version": REPLAN_CONTEXT_SCHEMA_VERSION,
        "context_id": context_id,
        "obligation_id": obligation_id,
        "evidence_source": "agent_scoped_evidence_log",
        "delivery": "host_projected",
        "coverage_ledger": coverage_ledger,
        "uncovered_frontier": uncovered_frontier,
        "delivery_receipt": {
            "schema_version": REPLAN_CONTEXT_RECEIPT_SCHEMA_VERSION,
            "context_id": context_id,
            "obligation_id": obligation_id,
            "status": "delivered",
            "delivered_by": "quota_host_projection",
        },
    }


def build_replan_action_packet(
    obligation: Mapping[str, Any],
    *,
    goal_id: str | None = None,
    agent_id: str | None = None,
    bounded_research_frontier: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = obligation.get("replan_context")
    if not isinstance(context, Mapping):
        raise TypeError("replan obligation is missing host-projected context")
    todo_actions = obligation.get("todo_actions")
    todo_action = next(
        (
            dict(item)
            for item in todo_actions or []
            if isinstance(item, Mapping)
            and item.get("action") == "add"
            and item.get("role") == "agent"
            and str(item.get("text") or "").strip()
        ),
        {},
    )
    successor_priority = str(todo_action.get("priority") or "P1").strip()
    if successor_priority not in {"P0", "P1", "P2", "P3", "P4"}:
        successor_priority = "P1"
    raw_selected_gap = (
        bounded_research_frontier.get("selected_gap")
        if isinstance(bounded_research_frontier, Mapping)
        else None
    )
    selected_gap: Mapping[str, Any] | None = (
        raw_selected_gap if isinstance(raw_selected_gap, Mapping) else None
    )
    selected_gap_values: Mapping[str, Any] = selected_gap or {}
    raw_binding = selected_gap_values.get("successor_binding")
    raw_successor_binding: Mapping[str, Any] = (
        raw_binding if isinstance(raw_binding, Mapping) else {}
    )
    successor_binding = replan_successor_semantic_binding(
        action_kind=raw_successor_binding.get("action_kind"),
        target_key=raw_successor_binding.get("target_key"),
        explore_result_node_refs=raw_successor_binding.get(
            "explore_result_node_refs"
        ),
    )
    writeback_contract = replan_writeback_requirements(obligation)["writeback_contract"]
    successor_summary = str(
        selected_gap_values.get("successor_summary") or ""
    ).strip()[:240]
    if selected_gap is not None and successor_summary and successor_binding:
        safe_goal_id = _stable_id(goal_id, field="goal_id") or "<goal-id>"
        safe_agent_id = (
            _stable_id(agent_id or obligation.get("agent_id"), field="agent_id")
            or "<agent-id>"
        )
        successor_text = shlex.quote(
            f"[{successor_priority}] {successor_summary}"
        )
        successor_ref_args = "".join(
            f" --explore-result-node-ref {shlex.quote(ref)}"
            for ref in successor_binding.get("explore_result_node_refs") or []
        )
        target_key_arg = (
            " --target-key " + shlex.quote(str(successor_binding["target_key"]))
            if successor_binding.get("target_key")
            else ""
        )
        task_domain = normalize_todo_task_domain(
            raw_successor_binding.get("task_domain")
        )
        task_domain_arg = (
            " --task-domain " + shlex.quote(task_domain) if task_domain else ""
        )
        writeback_contract = {
            "successor_command": (
                "loopx todo add "
                f"--goal-id {safe_goal_id} --role agent "
                "--task-class advancement_task "
                f"--action-kind {shlex.quote(str(successor_binding['action_kind']))}"
                f"{task_domain_arg}{target_key_arg} "
                f"--text {successor_text} "
                f"--claimed-by {safe_agent_id} "
                "--replan-obligation-id "
                f"{obligation.get('obligation_id')}"
                f"{successor_ref_args}"
            ),
            "successor_host_action": "end_current_heartbeat",
        }
    packet = {
        "schema_version": REPLAN_ACTION_PACKET_SCHEMA_VERSION,
        "decision": "replan_required",
        "obligation_id": obligation.get("obligation_id"),
        "uncovered_frontier": context.get("uncovered_frontier"),
        "required_outcome": "semantic_delta",
        "writeback_contract": writeback_contract,
        "allowed_terminal": [
            ProgressResultClass.EXPLORATION_EXHAUSTED.value,
            ProgressResultClass.BLOCKED.value,
            ProgressResultClass.NO_FOLLOWUP.value,
        ],
    }
    if isinstance(selected_gap, Mapping):
        packet["bounded_frontier"] = {
            key: selected_gap[key]
            for key in (
                "schema_version",
                "gap_id",
                "experiment_node_ref",
                "input_node_refs",
                "required_outcome",
            )
            if key in selected_gap
        }
    return packet
