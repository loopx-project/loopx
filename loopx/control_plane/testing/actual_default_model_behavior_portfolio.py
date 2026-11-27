"""Low-frequency actual-default one-arm model behavior portfolio runner."""

from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable, Mapping, Sequence

from .actual_default_model_behavior_catalog import (
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CATALOG_SCHEMA_VERSION,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_SCHEMA_VERSION,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_PORTFOLIO_SCHEMA_VERSION,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
    _CONTRASTS,
    _DIAGNOSTIC_CODE,
    _HARD_INVARIANT_FIELDS,
    _SCENARIOS,
    _TOOL_ACTOR_KINDS,
    _TURN_ACTOR_KINDS,
    JsonDict,
    _ContrastSpec,
    _ScenarioSpec,
    _canonical_json,
    _digest,
    _pretty_json_size,
    actual_default_model_behavior_scenario_catalog,
    dict_or_empty,
    sequence_or_empty,
)
from .actual_default_model_behavior_sources import (
    build_actual_default_model_behavior_scenario_inputs,
    build_quota_hot_path_compaction_regression_source,
)
from .actual_default_model_behavior_validators import (
    _contrast_relation_failures,
    _receipt_alignment,
    _scenario_contract,
    _tool_repeat_diagnostic,
    _turn_expected_contract,
    _validate_compaction_scenario,
    _validate_contrast_source_contracts,
    _validate_control_plane_composition_scenario,
    _validate_identity_scenario_contract,
    _validate_planning_context_scenario,
    _validate_planning_horizon_model_scenario,
    _validate_quota_hot_path_compaction_regression,
)
from .model_behavior_qualification import (
    ModelBehaviorActor,
    _actor_failure_code,
    run_model_behavior_qualification_arm,
)
from .onboarding_model_behavior_qualification import (
    OnboardingModelBehaviorActor,
    run_onboarding_model_behavior_phase,
)

__all__ = [
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_CATALOG_SCHEMA_VERSION",
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT",
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_SCHEMA_VERSION",
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET",
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_PORTFOLIO_SCHEMA_VERSION",
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS",
    "ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT",
    "JsonDict",
    "_CONTRASTS",
    "_DIAGNOSTIC_CODE",
    "_HARD_INVARIANT_FIELDS",
    "_SCENARIOS",
    "_TOOL_ACTOR_KINDS",
    "_TURN_ACTOR_KINDS",
    "_ContrastSpec",
    "_ScenarioSpec",
    "_canonical_json",
    "_contrast_relation_failures",
    "_digest",
    "_pretty_json_size",
    "_receipt_alignment",
    "_scenario_contract",
    "_scenario_result",
    "_tool_repeat_diagnostic",
    "_turn_expected_contract",
    "_validate_compaction_scenario",
    "_validate_contrast_source_contracts",
    "_validate_control_plane_composition_scenario",
    "_validate_identity_scenario_contract",
    "_validate_planning_context_scenario",
    "_validate_planning_horizon_model_scenario",
    "_validate_quota_hot_path_compaction_regression",
    "actual_default_model_behavior_scenario_catalog",
    "build_actual_default_model_behavior_scenario_inputs",
    "build_quota_hot_path_compaction_regression_source",
    "run_actual_default_model_behavior_portfolio",
]


def _scenario_result(
    spec: _ScenarioSpec,
    packet: Mapping[str, object],
    *,
    expected: Mapping[str, object],
    qualification_id: str,
    turn_actor: ModelBehaviorActor,
    onboarding_actor: OnboardingModelBehaviorActor,
    selected_todo_actor: Callable[[str], Mapping[str, object]],
    replan_semantic_action_actor: Callable[[str], Mapping[str, object]],
    scoped_gate_successor_actor: Callable[[str], Mapping[str, object]],
    capability_monitor_repair_actor: Callable[[str], Mapping[str, object]],
    terminal_settlement_actor: Callable[[str], Mapping[str, object]],
) -> tuple[JsonDict, bool, list[JsonDict]]:
    tool_actors: dict[str, Callable[[str], Mapping[str, object]]] = {
        "turn_tool": selected_todo_actor,
        "terminal_settlement_tool": terminal_settlement_actor,
        "replan_tool": replan_semantic_action_actor,
        "scoped_gate_tool": scoped_gate_successor_actor,
        "capability_repair_tool": capability_monitor_repair_actor,
    }
    receipt_digests: list[str] = []
    observed_routes: list[str] = []
    observed_action_kind_sequences: list[list[str]] = []
    failure_codes: list[str] = []
    repeat_diagnostics: list[JsonDict] = []
    actor_error = False
    observations: list[JsonDict] = []
    for repeat_index in range(ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS):
        run_id = f"{qualification_id}:{spec.scenario_id}:r{repeat_index + 1}"
        try:
            if (tool_actor := tool_actors.get(spec.actor_kind)) is not None:
                receipt = dict_or_empty(tool_actor(run_id))
                if receipt.get("qualification_passed") is not True:
                    failure_codes.append(
                        str(receipt.get("failure_code") or "tool_behavior_failed")
                    )
                observed_route = str(receipt.get("decision") or "")
            elif spec.actor_kind == "turn":
                receipt = dict_or_empty(
                    run_model_behavior_qualification_arm(
                        packet,
                        qualification_id=run_id,
                        arm="full_packet",
                        actor=turn_actor,
                        semantic_contract_required=bool(spec.semantic_contract_fields),
                        semantic_contract_fields=spec.semantic_contract_fields,
                    )
                )
                observed_route = str(receipt.get("decision") or "")
            else:
                receipt = dict_or_empty(
                    run_onboarding_model_behavior_phase(
                        packet,
                        qualification_id=run_id,
                        phase=str(spec.phase),
                        actor=onboarding_actor,
                    )
                )
                observed_route = str(receipt.get("next_action") or "")
        except (ValueError, RuntimeError) as exc:
            failure_codes.append(_actor_failure_code(exc))
            actor_error = True
            break
        aligned, mismatches = _receipt_alignment(spec, receipt, expected)
        receipt_digests.append(_digest(receipt))
        if spec.actor_kind in _TOOL_ACTOR_KINDS:
            repeat_diagnostics.append(_tool_repeat_diagnostic(receipt, repeat_index + 1))
        observations.append(
            {field: receipt.get(field) for field in _HARD_INVARIANT_FIELDS}
        )
        if observed_route not in observed_routes:
            observed_routes.append(observed_route)
        if spec.actor_kind == "turn":
            action_kind_sequence = [
                str(item)
                for item in sequence_or_empty(receipt.get("intended_action_kinds"))
            ]
            if (
                action_kind_sequence
                and action_kind_sequence not in observed_action_kind_sequences
            ):
                observed_action_kind_sequences.append(action_kind_sequence)
        if not aligned:
            failure_codes.extend(mismatches)
    repeats_completed = len(receipt_digests)
    passed = bool(
        not failure_codes
        and repeats_completed == ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS
    )
    result: JsonDict = {
        "scenario_id": spec.scenario_id,
        "actor_kind": spec.actor_kind,
        "phase": spec.phase,
        "expected_route": spec.expected_route,
        "status": "passed" if passed else "failed",
        "repeats_required": ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
        "repeats_completed": repeats_completed,
        "observed_routes": observed_routes,
        "observed_action_kind_sequences": observed_action_kind_sequences,
        "failure_codes": sorted(set(failure_codes)),
        "receipt_digests": receipt_digests,
    }
    if spec.actor_kind in _TOOL_ACTOR_KINDS:
        result["repeat_diagnostics"] = repeat_diagnostics
    return result, actor_error, observations


def _contrast_result(
    spec: _ContrastSpec,
    observations: Mapping[str, Sequence[Mapping[str, object]]],
) -> JsonDict:
    left = observations.get(spec.left_scenario_id, [])
    right = observations.get(spec.right_scenario_id, [])
    compared = min(len(left), len(right))
    failure_codes: list[str] = []
    observation_digests: list[str] = []
    if compared != ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS:
        failure_codes.append("contrast_scenarios_incomplete")
    for repeat_index in range(compared):
        failures = _contrast_relation_failures(
            spec,
            left[repeat_index],
            right[repeat_index],
        )
        failure_codes.extend(failures)
        observation_digests.append(
            _digest(
                {
                    "left": dict(left[repeat_index]),
                    "right": dict(right[repeat_index]),
                }
            )
        )
    passed = bool(
        not failure_codes and compared == ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS
    )
    return {
        "schema_version": ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_SCHEMA_VERSION,
        "contrast_id": spec.contrast_id,
        "contrast_kind": spec.contrast_kind,
        "left_scenario_id": spec.left_scenario_id,
        "right_scenario_id": spec.right_scenario_id,
        "must_match_fields": list(spec.must_match_fields),
        "must_differ_fields": list(spec.must_differ_fields),
        "status": "passed" if passed else "failed",
        "repeats_required": ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
        "repeats_compared": compared,
        "failure_codes": sorted(set(failure_codes)),
        "observation_digests": observation_digests,
    }


def _aborted_scenario_result(spec: _ScenarioSpec) -> JsonDict:
    return {
        "scenario_id": spec.scenario_id,
        "actor_kind": spec.actor_kind,
        "phase": spec.phase,
        "expected_route": spec.expected_route,
        "status": "not_run",
        "repeats_required": ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
        "repeats_completed": 0,
        "observed_routes": [],
        "failure_codes": ["portfolio_aborted_after_actor_error"],
        "receipt_digests": [],
    }


def _run_portfolio_scenarios(
    scenarios: tuple[_ScenarioSpec, ...],
    run_one: Callable[[_ScenarioSpec], tuple[JsonDict, bool, list[JsonDict]]],
    *,
    max_workers: int,
) -> tuple[list[JsonDict], dict[str, list[JsonDict]], int]:
    results: list[JsonDict] = []
    observations: dict[str, list[JsonDict]] = {}
    actor_call_count = 0
    if max_workers == 1:
        aborted = False
        for spec in scenarios:
            if aborted:
                results.append(_aborted_scenario_result(spec))
                continue
            result, actor_error, scenario_observations = run_one(spec)
            repeats = result.get("repeats_completed")
            actor_call_count += repeats if isinstance(repeats, int) else 0
            if actor_error:
                actor_call_count += 1
                aborted = True
            results.append(result)
            observations[spec.scenario_id] = scenario_observations
        return results, observations, actor_call_count

    lock = threading.Lock()
    aborted = False

    def _worker(
        spec: _ScenarioSpec,
    ) -> tuple[_ScenarioSpec, JsonDict, bool, list[JsonDict]]:
        nonlocal aborted
        with lock:
            if aborted:
                return spec, _aborted_scenario_result(spec), False, []
        result, actor_error, scenario_observations = run_one(spec)
        if actor_error:
            with lock:
                aborted = True
        return spec, result, actor_error, scenario_observations

    worker_count = min(max_workers, len(scenarios))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        completed = list(pool.map(_worker, scenarios))
    for spec, result, actor_error, scenario_observations in completed:
        repeats = result.get("repeats_completed")
        actor_call_count += (repeats if isinstance(repeats, int) else 0) + (
            1 if actor_error else 0
        )
        results.append(result)
        if result["status"] != "not_run":
            observations[spec.scenario_id] = scenario_observations
    return results, observations, actor_call_count


def run_actual_default_model_behavior_portfolio(
    scenario_packets: Mapping[str, Mapping[str, object]],
    *,
    scenario_sources: Mapping[str, Mapping[str, object]],
    qualification_id: str,
    turn_actor: ModelBehaviorActor,
    onboarding_actor: OnboardingModelBehaviorActor,
    selected_todo_actor: Callable[[str], Mapping[str, object]],
    replan_semantic_action_actor: Callable[[str], Mapping[str, object]],
    scoped_gate_successor_actor: Callable[[str], Mapping[str, object]],
    capability_monitor_repair_actor: Callable[[str], Mapping[str, object]],
    terminal_settlement_actor: Callable[[str], Mapping[str, object]],
    max_workers: int = 1,
) -> JsonDict:
    """Run the fixed low-frequency one-arm portfolio with bounded receipts."""
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        raise ValueError("max_workers must be a positive integer")
    expected_ids = {spec.scenario_id for spec in _SCENARIOS}
    supplied_ids = set(scenario_packets)
    if supplied_ids != expected_ids:
        missing = sorted(expected_ids - supplied_ids)
        unknown = sorted(supplied_ids - expected_ids)
        raise ValueError(
            "scenario packets must match the catalog; "
            f"missing={missing}, unknown={unknown}"
        )
    source_ids = set(scenario_sources)
    if source_ids != expected_ids:
        missing = sorted(expected_ids - source_ids)
        unknown = sorted(source_ids - expected_ids)
        raise ValueError(
            "scenario sources must match the catalog; "
            f"missing={missing}, unknown={unknown}"
        )

    catalog = actual_default_model_behavior_scenario_catalog()
    contracts = {
        spec.scenario_id: _scenario_contract(
            spec,
            scenario_sources[spec.scenario_id],
            scenario_packets[spec.scenario_id],
        )
        for spec in _SCENARIOS
    }
    _validate_contrast_source_contracts(contracts)

    def _run_one(
        spec: _ScenarioSpec,
    ) -> tuple[JsonDict, bool, list[JsonDict]]:
        return _scenario_result(
            spec,
            scenario_packets[spec.scenario_id],
            expected=contracts[spec.scenario_id],
            qualification_id=qualification_id,
            turn_actor=turn_actor,
            onboarding_actor=onboarding_actor,
            selected_todo_actor=selected_todo_actor,
            replan_semantic_action_actor=replan_semantic_action_actor,
            scoped_gate_successor_actor=scoped_gate_successor_actor,
            capability_monitor_repair_actor=capability_monitor_repair_actor,
            terminal_settlement_actor=terminal_settlement_actor,
        )

    results, observations, actor_call_count = _run_portfolio_scenarios(
        _SCENARIOS, _run_one, max_workers=max_workers
    )
    contrast_results = [_contrast_result(spec, observations) for spec in _CONTRASTS]
    passed = all(result["status"] == "passed" for result in results) and all(
        result["status"] == "passed" for result in contrast_results
    )
    scenario_failure_count = sum(result["status"] == "failed" for result in results)
    contrast_failure_count = sum(
        result["status"] == "failed" for result in contrast_results
    )
    skip_count = sum(result["status"] == "not_run" for result in results)
    tool_enabled_scenario_count = sum(
        spec.actor_kind in _TOOL_ACTOR_KINDS for spec in _SCENARIOS
    )
    return {
        "schema_version": ACTUAL_DEFAULT_MODEL_BEHAVIOR_PORTFOLIO_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "topology": "actual_default_one_arm",
        "scenario_catalog_digest": _digest(catalog),
        "scenario_count": ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
        "contrast_count": ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
        "actor_call_budget": (
            ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT
            * ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS
        ),
        "actor_call_count": actor_call_count,
        "failure_count": scenario_failure_count + contrast_failure_count,
        "skip_count": skip_count,
        "contrast_failure_count": contrast_failure_count,
        "qualification_passed": passed,
        "automatic_release_promotion_allowed": False,
        "scenarios": results,
        "contrasts": contrast_results,
        "boundary": {
            "tools_enabled": True,
            "tool_enabled_scenario_count": tool_enabled_scenario_count,
            "packet_interpretation_scenario_count": (
                ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT
                - tool_enabled_scenario_count
            ),
            "raw_packets_persisted": False,
            "raw_model_responses_persisted": False,
            "automatic_retries": False,
        },
    }
