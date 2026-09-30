"""Factorial contrast read model for benchmark experiment-board rows."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .four_arm_contract import (
    BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION,
    BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE,
)
from ...control_plane.content_digest import BARE_SHA256_PATTERN

BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION = "benchmark_factorial_contrast_v0"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_FACTOR_CELLS = {(False, False), (True, False), (False, True), (True, True)}


def _token(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field} must be a compact public-safe token")
    return text


def _optional_token(value: Any, *, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _token(value, field=field)


def build_benchmark_metric_delta(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare two normalized experiment-board metric values."""

    delta = float(candidate["value"]) - float(baseline["value"])
    result: dict[str, Any] = {
        "baseline_value": baseline["value"],
        "candidate_value": candidate["value"],
        "delta": delta,
    }
    baseline_total = baseline.get("total")
    candidate_total = candidate.get("total")
    if (
        isinstance(baseline_total, (int, float))
        and not isinstance(baseline_total, bool)
        and baseline_total > 0
        and isinstance(candidate_total, (int, float))
        and not isinstance(candidate_total, bool)
        and candidate_total > 0
    ):
        baseline_rate = float(baseline["value"]) / float(baseline_total)
        candidate_rate = float(candidate["value"]) / float(candidate_total)
        result.update(
            {
                "baseline_total": baseline_total,
                "candidate_total": candidate_total,
                "baseline_rate": baseline_rate,
                "candidate_rate": candidate_rate,
                "delta_rate": candidate_rate - baseline_rate,
            }
        )
    higher_is_better = candidate.get("higher_is_better")
    if (
        isinstance(higher_is_better, bool)
        and baseline.get("higher_is_better") == higher_is_better
    ):
        if delta == 0:
            result["direction"] = "flat"
        elif (delta > 0) == higher_is_better:
            result["direction"] = "improved"
        else:
            result["direction"] = "regressed"
    return result


def _normalize_four_arm_design(contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        raise TypeError("four-arm contract must be an object")
    if contract.get("schema_version") != BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION:
        raise ValueError("four-arm contract schema mismatch")
    if contract.get("qualified") is not True:
        raise ValueError("four-arm contract must be design-qualified")
    if contract.get("qualification_scope") != BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE:
        raise ValueError("four-arm contract qualification scope mismatch")
    attestations = contract.get("attestations")
    if (
        not isinstance(attestations, Mapping)
        or attestations.get("domain_hint_independent_of_loopx") is not True
    ):
        raise ValueError("four-arm domain hint independence is not attested")
    if contract.get("factors") != {
        "loopx": [False, True],
        "domain_hint": [False, True],
    }:
        raise ValueError("four-arm contract factors are unsupported")

    raw_arms = contract.get("arms")
    if not isinstance(raw_arms, list) or len(raw_arms) != 4:
        raise ValueError("four-arm contract must declare exactly four arms")
    cells: dict[tuple[bool, bool], dict[str, Any]] = {}
    arms_by_id: dict[str, dict[str, Any]] = {}
    for raw_arm in raw_arms:
        if not isinstance(raw_arm, Mapping):
            raise TypeError("four-arm contract arms must be objects")
        arm_id = _token(raw_arm.get("arm_id"), field="four-arm arm_id")
        loopx_enabled = raw_arm.get("loopx_enabled")
        hint_enabled = raw_arm.get("domain_hint_enabled")
        if not isinstance(loopx_enabled, bool) or not isinstance(hint_enabled, bool):
            raise TypeError("four-arm factors must be boolean")
        cell_key = (loopx_enabled, hint_enabled)
        if cell_key in cells or arm_id in arms_by_id:
            raise ValueError("four-arm contract cells and arm ids must be unique")
        expected_role = (
            "baseline"
            if cell_key == (False, False)
            else "control"
            if cell_key == (False, True)
            else "treatment"
        )
        arm_role = _token(raw_arm.get("arm_role"), field="four-arm arm_role")
        if arm_role != expected_role:
            raise ValueError("four-arm contract role does not match its factor cell")
        task_goal_sha256 = str(raw_arm.get("task_goal_sha256") or "").strip()
        if not BARE_SHA256_PATTERN.fullmatch(task_goal_sha256):
            raise ValueError("four-arm task-goal hash must be sha256")
        arm = {
            "arm_id": arm_id,
            "arm_role": arm_role,
            "loopx_enabled": loopx_enabled,
            "domain_hint_enabled": hint_enabled,
            "task_goal_sha256": task_goal_sha256,
        }
        anchor_id = _optional_token(
            raw_arm.get("comparison_anchor_arm_id"),
            field="four-arm comparison_anchor_arm_id",
        )
        if anchor_id is not None:
            arm["comparison_anchor_arm_id"] = anchor_id
        cells[cell_key] = arm
        arms_by_id[arm_id] = arm

    if set(cells) != _FACTOR_CELLS:
        raise ValueError("four-arm contract is missing a factor cell")
    expected_anchors = {
        (True, False): cells[(False, False)]["arm_id"],
        (False, True): cells[(False, False)]["arm_id"],
        (True, True): cells[(False, True)]["arm_id"],
    }
    if "comparison_anchor_arm_id" in cells[(False, False)]:
        raise ValueError("plain Goal cell cannot declare a comparison anchor")
    for cell_key, expected_anchor in expected_anchors.items():
        if cells[cell_key].get("comparison_anchor_arm_id") != expected_anchor:
            raise ValueError("four-arm comparison anchor does not match factor design")
    if (
        cells[(False, False)]["task_goal_sha256"]
        != cells[(True, False)]["task_goal_sha256"]
    ):
        raise ValueError("plain Goal and LoopX cells must have prompt parity")
    if (
        cells[(False, True)]["task_goal_sha256"]
        != cells[(True, True)]["task_goal_sha256"]
    ):
        raise ValueError("hinted Goal and LoopX cells must have prompt parity")
    if (
        cells[(False, False)]["task_goal_sha256"]
        == cells[(False, True)]["task_goal_sha256"]
    ):
        raise ValueError("plain and hinted task goals must be distinct")

    expected_effects = {
        "loopx_without_domain_hint": (
            cells[(True, False)]["arm_id"],
            cells[(False, False)]["arm_id"],
        ),
        "domain_hint_without_loopx": (
            cells[(False, True)]["arm_id"],
            cells[(False, False)]["arm_id"],
        ),
        "loopx_with_domain_hint": (
            cells[(True, True)]["arm_id"],
            cells[(False, True)]["arm_id"],
        ),
    }
    raw_effects = contract.get("primary_comparisons")
    if not isinstance(raw_effects, list) or len(raw_effects) != 3:
        raise ValueError("four-arm contract must declare three primary comparisons")
    effects: list[dict[str, str]] = []
    observed_effects: dict[str, tuple[str, str]] = {}
    for raw_effect in raw_effects:
        if not isinstance(raw_effect, Mapping):
            raise TypeError("four-arm primary comparisons must be objects")
        effect = _token(raw_effect.get("effect"), field="four-arm effect")
        pair = (
            _token(
                raw_effect.get("candidate_arm_id"),
                field="four-arm candidate_arm_id",
            ),
            _token(raw_effect.get("anchor_arm_id"), field="four-arm anchor_arm_id"),
        )
        if effect in observed_effects or pair in observed_effects.values():
            raise ValueError("four-arm primary comparisons must be unique")
        if pair[0] not in arms_by_id or pair[1] not in arms_by_id:
            raise ValueError("four-arm primary comparison names an unknown arm")
        observed_effects[effect] = pair
        effects.append(
            {
                "effect": effect,
                "candidate_arm_id": pair[0],
                "anchor_arm_id": pair[1],
            }
        )
    if observed_effects != expected_effects:
        raise ValueError("four-arm primary comparisons do not match factor design")

    raw_interaction = contract.get("interaction_contrast")
    if not isinstance(raw_interaction, Mapping):
        raise TypeError("four-arm interaction contrast must be an object")

    def interaction_pair(field: str) -> tuple[str, str]:
        raw_pair = raw_interaction.get(field)
        if not isinstance(raw_pair, Mapping):
            raise TypeError(f"four-arm interaction {field} must be an object")
        pair = (
            _token(
                raw_pair.get("candidate_arm_id"),
                field=f"four-arm interaction {field} candidate_arm_id",
            ),
            _token(
                raw_pair.get("anchor_arm_id"),
                field=f"four-arm interaction {field} anchor_arm_id",
            ),
        )
        if pair not in observed_effects.values():
            raise ValueError("four-arm interaction names an unknown primary effect")
        return pair

    interaction = {
        "candidate_effect_pair": interaction_pair("candidate_effect"),
        "anchor_effect_pair": interaction_pair("anchor_effect"),
    }
    if interaction != {
        "candidate_effect_pair": expected_effects["loopx_with_domain_hint"],
        "anchor_effect_pair": expected_effects["loopx_without_domain_hint"],
    }:
        raise ValueError("four-arm interaction contrast does not match factor design")
    return {
        "arms_by_id": arms_by_id,
        "effects": effects,
        "interaction": interaction,
    }


def _interaction_metric(
    *,
    candidate_effect: Mapping[str, Any],
    anchor_effect: Mapping[str, Any],
    higher_is_better: bool | None,
) -> dict[str, Any]:
    candidate_delta = candidate_effect.get("delta")
    anchor_delta = anchor_effect.get("delta")
    result: dict[str, Any] = {
        "candidate_effect_delta": candidate_delta,
        "anchor_effect_delta": anchor_delta,
    }
    if isinstance(candidate_delta, (int, float)) and isinstance(
        anchor_delta, (int, float)
    ):
        difference = candidate_delta - anchor_delta
        result["difference_in_differences"] = difference
        if difference == 0:
            result["direction"] = "flat"
        elif isinstance(higher_is_better, bool):
            result["direction"] = (
                "improved" if (difference > 0) == higher_is_better else "regressed"
            )
    candidate_rate = candidate_effect.get("delta_rate")
    anchor_rate = anchor_effect.get("delta_rate")
    if isinstance(candidate_rate, (int, float)) and isinstance(
        anchor_rate, (int, float)
    ):
        result["candidate_effect_delta_rate"] = candidate_rate
        result["anchor_effect_delta_rate"] = anchor_rate
        result["difference_in_differences_rate"] = candidate_rate - anchor_rate
    return result


def build_benchmark_factorial_contrasts(
    rows: Iterable[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project conditional effects from explicitly declared four-arm cells."""

    design = _normalize_four_arm_design(contract)
    arms_by_id = design["arms_by_id"]
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("arm_id") not in arms_by_id:
            continue
        key = (
            str(row.get("benchmark_id")),
            str(row.get("study_id")),
            str(row.get("case_id")),
        )
        grouped.setdefault(key, []).append(row)

    contrasts: list[dict[str, Any]] = []
    for (benchmark_id, study_id, case_id), case_rows in sorted(grouped.items()):
        reasons: list[str] = []
        selected: dict[str, Mapping[str, Any]] = {}
        cell_status: list[dict[str, Any]] = []
        cells_by_arm_id: dict[str, dict[str, Any]] = {}
        for arm_id, arm_contract in arms_by_id.items():
            candidates = [row for row in case_rows if row.get("arm_id") == arm_id]
            countable = [
                row
                for row in candidates
                if row.get("countability", {}).get("score_countable") is True
            ]
            cell_reasons: list[str] = []
            if not countable:
                cell_reasons.append("no_score_countable_run")
                reasons.append("cell_has_no_score_countable_run")
            elif len(countable) > 1:
                cell_reasons.append("ambiguous_score_countable_runs")
                reasons.append("cell_has_ambiguous_score_countable_runs")
            else:
                selected[arm_id] = countable[0]
            cell = {
                "arm_id": arm_id,
                "arm_role": arm_contract["arm_role"],
                "loopx_enabled": arm_contract["loopx_enabled"],
                "domain_hint_enabled": arm_contract["domain_hint_enabled"],
                "run_count": len(candidates),
                "score_countable_run_count": len(countable),
                "selected_run_id": countable[0].get("run_id")
                if len(countable) == 1
                else None,
                "reason_codes": cell_reasons,
            }
            cell_status.append(cell)
            cells_by_arm_id[arm_id] = cell

        if len(selected) == len(arms_by_id):
            _qualify_selected_cells(
                selected=selected,
                arms_by_id=arms_by_id,
                cells_by_arm_id=cells_by_arm_id,
                reasons=reasons,
            )

        effects, effect_by_pair = _conditional_effects(
            selected=selected,
            design=design,
        )
        interaction_metrics = _interaction_metrics(
            selected=selected,
            design=design,
            effect_by_pair=effect_by_pair,
        )
        contrasts.append(
            {
                "schema_version": BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION,
                "benchmark_id": benchmark_id,
                "study_id": study_id,
                "case_id": case_id,
                "primary_metric": (
                    next(iter(selected.values())).get("primary_metric")
                    if selected
                    else None
                ),
                "factorial_contrast_countable": not reasons,
                "qualification_scope": "declared_factor_design_and_board_results",
                "reason_codes": sorted(set(reasons)),
                "cells": cell_status,
                "conditional_effects": effects,
                "interaction_contrast": {
                    "candidate_effect_pair": list(
                        design["interaction"]["candidate_effect_pair"]
                    ),
                    "anchor_effect_pair": list(
                        design["interaction"]["anchor_effect_pair"]
                    ),
                    "metric_contrasts": interaction_metrics,
                },
            }
        )
    return contrasts


def _qualify_selected_cells(
    *,
    selected: Mapping[str, Mapping[str, Any]],
    arms_by_id: Mapping[str, Mapping[str, Any]],
    cells_by_arm_id: Mapping[str, dict[str, Any]],
    reasons: list[str],
) -> None:
    def reject_cell(arm_id: str, reason: str) -> None:
        cells_by_arm_id[arm_id]["reason_codes"].append(reason)
        reasons.append(f"cell_{reason}")

    selected_rows = list(selected.values())
    for arm_id, row in selected.items():
        arm_contract = arms_by_id[arm_id]
        if row.get("arm_role") != arm_contract["arm_role"]:
            reject_cell(arm_id, "role_mismatch")
        expected_fidelity = (
            "not_applicable" if arm_contract["arm_role"] == "baseline" else "qualified"
        )
        if row.get("treatment_fidelity") != expected_fidelity:
            reject_cell(arm_id, "treatment_fidelity_not_qualified")
        if row.get("claim_scope") != "matched_study":
            reject_cell(arm_id, "claim_scope_not_matched_study")
        anchor_arm_id = arm_contract.get("comparison_anchor_arm_id")
        if anchor_arm_id is not None and row.get(
            "comparison_anchor_run_id"
        ) != selected[anchor_arm_id].get("run_id"):
            reject_cell(arm_id, "comparison_anchor_run_mismatch")

    for field in ("model_id", "comparison_protocol_id", "primary_metric"):
        values = [row.get(field) for row in selected_rows]
        if any(value != values[0] for value in values[1:]):
            reasons.append(f"{field}_mismatch")
    runner_revisions = [row.get("runner_revision") for row in selected_rows]
    if any(value in (None, "") for value in runner_revisions):
        reasons.append("runner_revision_missing")
    elif any(value != runner_revisions[0] for value in runner_revisions[1:]):
        reasons.append("runner_revision_mismatch")

    loopx_runtimes = [
        selected[arm["arm_id"]].get("orchestrator_runtime")
        for arm in arms_by_id.values()
        if arm["loopx_enabled"]
    ]
    if any(runtime is None for runtime in loopx_runtimes):
        reasons.append("loopx_orchestrator_runtime_missing")
    elif loopx_runtimes[0] != loopx_runtimes[1]:
        reasons.append("loopx_orchestrator_runtime_mismatch")
    goal_runtimes = [
        selected[arm["arm_id"]].get("orchestrator_runtime")
        for arm in arms_by_id.values()
        if not arm["loopx_enabled"]
    ]
    if goal_runtimes[0] != goal_runtimes[1]:
        reasons.append("goal_orchestrator_runtime_mismatch")
    elif (
        loopx_runtimes
        and all(runtime is not None for runtime in loopx_runtimes)
        and goal_runtimes[0] == loopx_runtimes[0]
    ):
        reasons.append("factor_runtime_cohorts_not_distinct")

    primary_metric = selected_rows[0]["primary_metric"]
    metric_signatures = [
        {
            key: value
            for key, value in row["metrics"][primary_metric].items()
            if key != "value"
        }
        for row in selected_rows
    ]
    if any(signature != metric_signatures[0] for signature in metric_signatures[1:]):
        reasons.append("primary_metric_definition_mismatch")


def _conditional_effects(
    *,
    selected: Mapping[str, Mapping[str, Any]],
    design: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    if len(selected) != len(design["arms_by_id"]):
        return [], {}
    effects: list[dict[str, Any]] = []
    effect_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for effect in design["effects"]:
        candidate_arm_id = effect["candidate_arm_id"]
        anchor_arm_id = effect["anchor_arm_id"]
        candidate = selected[candidate_arm_id]
        anchor = selected[anchor_arm_id]
        metric_deltas = {
            name: build_benchmark_metric_delta(
                anchor["metrics"][name], candidate["metrics"][name]
            )
            for name in sorted(set(anchor["metrics"]) & set(candidate["metrics"]))
        }
        result = {
            **effect,
            "candidate_run_id": candidate.get("run_id"),
            "anchor_run_id": anchor.get("run_id"),
            "metric_deltas": metric_deltas,
        }
        effects.append(result)
        effect_by_pair[(candidate_arm_id, anchor_arm_id)] = result
    return effects, effect_by_pair


def _interaction_metrics(
    *,
    selected: Mapping[str, Mapping[str, Any]],
    design: Mapping[str, Any],
    effect_by_pair: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    if not effect_by_pair:
        return {}
    candidate_effect = effect_by_pair[design["interaction"]["candidate_effect_pair"]]
    anchor_effect = effect_by_pair[design["interaction"]["anchor_effect_pair"]]
    candidate_metrics = candidate_effect["metric_deltas"]
    anchor_metrics = anchor_effect["metric_deltas"]
    interaction_metrics: dict[str, Any] = {}
    for name in sorted(set(candidate_metrics) & set(anchor_metrics)):
        metric_source = selected[candidate_effect["candidate_arm_id"]]["metrics"][name]
        interaction_metrics[name] = _interaction_metric(
            candidate_effect=candidate_metrics[name],
            anchor_effect=anchor_metrics[name],
            higher_is_better=metric_source.get("higher_is_better"),
        )
    return interaction_metrics
