"""Thin Python transport & canonical log reader for Explore quantitative planning
and the 5-stage methodology operator catalog. All math, validation, and
invariants live in TypeScript under loopx/control_plane/capabilities/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value
from .result_log import (
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
    validate_explore_result_event,
)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _call_ts_owner(
    method: str,
    params: dict[str, Any],
    *,
    path: str,
    large_local_snapshot: bool = False,
) -> dict[str, Any]:
    validate_public_safe_value(params, path=path)
    try:
        result = effect_runtime_result(
            method,
            params,
            large_local_snapshot=large_local_snapshot,
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    return cast(dict[str, Any], result)


def evaluate_scoped_yardstick_fidelity(params: dict[str, Any]) -> dict[str, Any]:
    return _call_ts_owner(
        "explore.yardstick_fidelity.evaluate",
        params,
        path="yardstick_fidelity",
        large_local_snapshot=True,
    )


def evaluate_evolutionary_population(params: dict[str, Any]) -> dict[str, Any]:
    return _call_ts_owner(
        "explore.evolutionary_population.evaluate",
        params,
        path="evolutionary_population",
        large_local_snapshot=True,
    )


def _load_scoped_yardstick_targets(
    *,
    runtime_root: Path,
    goal_id: str,
    node_id: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    profiles_override: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_events = load_explore_result_events_strict(
        explore_result_log_path(runtime_root, goal_id),
        goal_id=goal_id,
    )
    validated = [
        validate_explore_result_event(event, expected_goal_id=goal_id)
        for event in raw_events
    ]
    projection = build_explore_result_projection(
        validated,
        goal_id=goal_id,
        finding_limit=max(1, len(validated)),
    )
    findings = [
        row
        for row in projection.get("findings") or []
        if isinstance(row, dict)
    ]

    targets: list[dict[str, Any]] = []
    if profiles_override is not None:
        if not isinstance(profiles_override, dict):
            raise ValueError("--profiles-json must contain a JSON object")
        resolved_node = str(node_id or profiles_override.get("node_id") or "").strip()
        if not resolved_node:
            raise ValueError("--node-id or profiles.node_id is required with --profiles-json")
        targets.append(
            {
                "node_id": resolved_node,
                "authoritative": profiles_override.get("authoritative"),
                "proxy": profiles_override.get("proxy"),
                "mechanism_by_candidate": profiles_override.get("mechanism_by_candidate"),
                "source_finding_id": None,
                "todo_id": todo_id,
            }
        )
        return findings, targets

    seen_keys: set[str] = set()
    for row in findings:
        capture = row.get("work_yardstick")
        if not isinstance(capture, dict):
            continue
        row_node = str(row.get("node_id") or "").strip()
        if node_id and row_node != node_id:
            continue
        binding = _as_dict(capture.get("binding"))
        if todo_id and binding.get("todo_id") != todo_id:
            continue
        if agent_id and binding.get("agent_id") != agent_id:
            continue
        decision = capture.get("decision")
        if not isinstance(decision, dict):
            continue
        dedupe_key = json.dumps(
            [row_node, decision.get("authoritative"), decision.get("proxy")],
            sort_keys=True,
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        targets.append(
            {
                "node_id": row_node,
                "authoritative": decision.get("authoritative"),
                "proxy": decision.get("proxy"),
                "mechanism_by_candidate": None,
                "source_finding_id": row.get("finding_id"),
                "todo_id": binding.get("todo_id"),
            }
        )
    return findings, targets


def build_goal_yardstick_fidelity(
    *,
    runtime_root: Path,
    goal_id: str,
    node_id: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    frontier_k: int | None = None,
    profiles_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings, targets = _load_scoped_yardstick_targets(
        runtime_root=runtime_root,
        goal_id=goal_id,
        node_id=node_id,
        todo_id=todo_id,
        agent_id=agent_id,
        profiles_override=profiles_override,
    )
    evaluations: list[dict[str, Any]] = []
    for target in targets:
        req: dict[str, Any] = {
            "node_id": target["node_id"],
            "authoritative": target["authoritative"],
            "proxy": target["proxy"],
            "findings": [f for f in findings if f.get("node_id") == target["node_id"]],
        }
        if frontier_k is not None:
            req["frontier_k"] = frontier_k
        evaluated = evaluate_scoped_yardstick_fidelity(req)
        evaluations.append(
            {
                **evaluated,
                "source_finding_id": target["source_finding_id"],
                "todo_id": target["todo_id"],
            }
        )

    return {
        "ok": True,
        "schema_version": "loopx_explore_yardstick_fidelity_report_v0",
        "goal_id": goal_id,
        "evaluation_count": len(evaluations),
        "evaluations": evaluations,
        "advisory_only": True,
        "grants_execution_authority": False,
    }


def build_goal_evolutionary_population(
    *,
    runtime_root: Path,
    goal_id: str,
    node_id: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    elite_limit: int | None = None,
    crossover_limit: int | None = None,
    mutation_limit: int | None = None,
    profiles_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings, targets = _load_scoped_yardstick_targets(
        runtime_root=runtime_root,
        goal_id=goal_id,
        node_id=node_id,
        todo_id=todo_id,
        agent_id=agent_id,
        profiles_override=profiles_override,
    )
    plans: list[dict[str, Any]] = []
    for target in targets:
        req: dict[str, Any] = {
            "node_id": target["node_id"],
            "authoritative": target["authoritative"],
            "proxy": target["proxy"],
            "findings": [f for f in findings if f.get("node_id") == target["node_id"]],
        }
        if elite_limit is not None:
            req["elite_limit"] = elite_limit
        if crossover_limit is not None:
            req["crossover_limit"] = crossover_limit
        if mutation_limit is not None:
            req["mutation_limit"] = mutation_limit
        if target.get("mechanism_by_candidate") is not None:
            req["mechanism_by_candidate"] = target["mechanism_by_candidate"]
        evaluated = evaluate_evolutionary_population(req)
        plans.append(
            {
                **evaluated,
                "source_finding_id": target["source_finding_id"],
                "todo_id": target["todo_id"],
            }
        )

    return {
        "ok": True,
        "schema_version": "loopx_explore_evolutionary_population_report_v0",
        "goal_id": goal_id,
        "plan_count": len(plans),
        "plans": plans,
        "advisory_only": True,
        "grants_execution_authority": False,
    }


def evaluate_budgeted_continuation_plan(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("budgeted continuation document must be a JSON object")
    result = _call_ts_owner(
        "explore.budgeted_continuation.evaluate",
        document,
        path="budgeted_continuation",
        large_local_snapshot=False,
    )
    return {"ok": True, **result}


def evaluate_counterfactual_continuations(
    document: dict[str, Any],
    *,
    strict_identification: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("counterfactual continuation document must be a JSON object")
    req = dict(document)
    if strict_identification is not None:
        req["strict_identification"] = bool(strict_identification)
    result = _call_ts_owner(
        "explore.counterfactual_continuation.evaluate",
        req,
        path="counterfactual_continuation",
        large_local_snapshot=True,
    )
    return {"ok": True, **result}


def list_stage_methodology_operators(*, stage: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if stage is not None:
        params["stage"] = stage
    result = _call_ts_owner(
        "methodology.operators.catalog",
        params,
        path="methodology_operators",
        large_local_snapshot=False,
    )
    return {"ok": True, **result}


def verify_methodology_operator_output(
    *,
    operator_id: str,
    output: dict[str, Any],
    max_bytes: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"operator_id": operator_id, "output": output}
    if max_bytes is not None:
        params["max_bytes"] = max_bytes
    result = _call_ts_owner(
        "methodology.operator.verify_output",
        params,
        path="methodology_verification",
        large_local_snapshot=False,
    )
    return {"ok": True, **result}


def render_quantitative_planning_markdown(payload: dict[str, object]) -> str | None:
    schema = str(payload.get("schema_version") or "")
    if schema == "loopx_explore_yardstick_fidelity_report_v0":
        rows = _as_list(payload.get("evaluations"))
        lines = [
            f"# Scoped Yardstick Fidelity: {payload.get('goal_id')}",
            "",
            f"- Evaluations: {len(rows)} (`advisory_only=true`)",
        ]
        for item in rows:
            if not isinstance(item, dict):
                continue
            counts = _as_dict(item.get("counts"))
            rc = _as_dict(item.get("rank_concordance"))
            fn = _as_dict(item.get("frontier_neighborhood"))
            lines.extend(
                [
                    "",
                    f"## Node `{item.get('node_id')}` -> `{item.get('fidelity_status')}`",
                    f"- Paired candidates: {counts.get('paired_candidates', 0)} "
                    f"(proxy-only: {counts.get('proxy_only_candidates', 0)}, "
                    f"authoritative-only: {counts.get('authoritative_only_candidates', 0)})",
                    f"- Kendall tau-b: {rc.get('kendall_tau_b')} | Frontier tau-b: {fn.get('frontier_kendall_tau_b')}",
                ]
            )
        lines.append("")
        return "\n".join(lines)

    if schema == "budgeted_continuation_plan_v0":
        conf = _as_dict(payload.get("confirmation"))
        routes = _as_list(payload.get("candidate_routes"))
        lines = [
            "# Budgeted Continuation Plan",
            "",
            f"- Latest confirmation start minute: `{conf.get('latest_confirmation_start_minute')}`",
            f"- Remaining work window minutes: `{conf.get('remaining_work_window_minutes')}`",
            f"- Effective safe baseline gain: `{payload.get('effective_safe_gain')}`",
            "",
            "## Candidate Routes",
            "",
        ]
        for route in routes:
            if isinstance(route, dict):
                lines.append(
                    f"- `{route.get('route_id')}` (`{route.get('feasibility_status')}` -> "
                    f"`{route.get('recommended_disposition')}`): slack={route.get('slack_before_deadline_minutes')}m, "
                    f"p*={route.get('p_star_at_expected_gain')}"
                )
        lines.append("")
        return "\n".join(lines)

    if schema == "counterfactual_continuation_eval_v0":
        counts = _as_dict(payload.get("counts"))
        est = _as_dict(payload.get("estimand_summary"))
        lines = [
            "# Same-Checkpoint Counterfactual Continuation Evaluation",
            "",
            f"- Pairs: {counts.get('valid_pairs', 0)} valid / {counts.get('total_pairs', 0)} total "
            f"(contaminated: {counts.get('contaminated_pairs', 0)})",
            f"- Action diverged: {counts.get('action_diverged_pairs', 0)} | "
            f"Wins/Ties/Losses: {counts.get('wins', 0)}/{counts.get('ties', 0)}/{counts.get('losses', 0)}",
            f"- Mean tau_B: `{est.get('mean_tau_b')}` | Sign-flip p-value: `{est.get('exact_sign_flip_p_value')}`",
            "",
        ]
        return "\n".join(lines)

    if schema == "loopx_explore_evolutionary_population_report_v0":
        rows = _as_list(payload.get("plans"))
        lines = [
            f"# Evolutionary Population Search Plan: {payload.get('goal_id')}",
            "",
            f"- Plans: {len(rows)} (`advisory_only=true`)",
        ]
        for item in rows:
            if not isinstance(item, dict):
                continue
            counts = _as_dict(item.get("counts"))
            lines.extend(
                [
                    "",
                    f"## Node `{item.get('node_id')}` -> `{item.get('selection_status')}` "
                    f"(proxy=`{item.get('proxy_fidelity_status')}`)",
                    f"- Confirmed candidates: {counts.get('confirmed_candidates', 0)} across "
                    f"{counts.get('mechanism_islands', 0)} mechanism islands",
                    f"- Elites: {counts.get('selected_elites', 0)} | "
                    f"Crossovers: {counts.get('crossover_pairs', 0)} | "
                    f"Mutations: {counts.get('mutation_targets', 0)} | "
                    f"Quarantined proxy winners: {counts.get('quarantined_proxy_candidates', 0)}",
                ]
            )
        lines.append("")
        return "\n".join(lines)

    if schema == "stage_methodology_operator_catalog_v0":
        ops = _as_list(payload.get("operators"))
        lines = [
            "# 5-Stage Methodology Operator Catalog",
            "",
        ]
        for op in ops:
            if isinstance(op, dict):
                lines.append(
                    f"- `{op.get('operator_id')}` (stage=`{op.get('stage')}`, gate=`{op.get('enabled_gate')}`, "
                    f"quiet=`{op.get('quiet_turn_policy')}`): {op.get('lightweight_guidance')}"
                )
        lines.append("")
        return "\n".join(lines)

    return None
