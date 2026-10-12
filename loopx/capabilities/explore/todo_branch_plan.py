from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from ...control_plane.todos.contract import (
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_status,
)
from ...control_plane.todos.todo_semantics import (
    todo_item_is_actionable_open,
    todo_item_task_class,
    todo_priority_rank,
    todo_projection_sort_key,
)
from ...control_plane.work_items.task_lease import scope_root, write_scopes_overlap
from .harness_gate import (
    GATE_STATE_ANALYSIS_ONLY,
    GATE_STATE_DISABLED,
    explore_harness_required_contract,
    resolve_explore_harness_gate as _resolve_explore_harness_gate,
)
from .speculative_scheduler import (
    build_branch_plan_ab_result,
    partition_invalidated_successors,
    schedule_confidence_prefix,
)
from .resource_portfolio import (
    resource_assignment,
    resource_lane_from_capabilities,
    resource_portfolio_with_selection,
    resolve_resource_portfolio,
)
from .todo_evidence import build_todo_typed_evidence_audit


TODO_BRANCH_PLAN_SCHEMA_VERSION = "loopx_explore_todo_branch_plan_v0"
DEFAULT_BRANCH_WIDTH = 3
MAX_BRANCH_WIDTH = 8
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_:\-]{3,}")
SHARED_DEPENDENCY_CAPABILITY_PREFIXES = (
    "shared_artifact:",
    "shared_implementation:",
)


def _tokenize(value: Any) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(str(value or ""))}


def _frontier_tokens(projection: Mapping[str, Any] | None) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(projection, Mapping):
        return tokens
    for lane in ("frontier", "stuck"):
        for item in projection.get(lane) or []:
            if not isinstance(item, Mapping):
                continue
            tokens.update(_tokenize(item.get("node_id")))
            tokens.update(_tokenize(item.get("title")))
            tokens.update(_tokenize(item.get("summary")))
            for tag in item.get("tags") or []:
                tokens.update(_tokenize(tag))
    return tokens


def _compact_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _required_write_scopes(item: Mapping[str, Any]) -> list[str]:
    return normalize_required_write_scopes(item.get("required_write_scopes"))


def _required_capabilities(item: Mapping[str, Any]) -> list[str]:
    return normalize_required_capabilities(item.get("required_capabilities"))


def _shared_dependency_capabilities(item: Mapping[str, Any]) -> list[str]:
    return [
        capability
        for capability in _required_capabilities(item)
        if capability.startswith(SHARED_DEPENDENCY_CAPABILITY_PREFIXES)
    ]


def _scopes_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    if write_scopes_overlap(list(left), list(right)):
        return True
    for left_scope in normalize_required_write_scopes(left):
        for right_scope in normalize_required_write_scopes(right):
            left_root = scope_root(left_scope)
            right_root = scope_root(right_scope)
            if left_root and left_root == right_root:
                return True
    return False


def _branch_score(
    item: Mapping[str, Any],
    *,
    agent_id: str | None,
    frontier: set[str],
) -> tuple[float, list[str], list[str]]:
    reason_codes: list[str] = []
    hazards: list[str] = []
    score = 0.0

    status = normalize_todo_status(item.get("status")) or (
        TODO_STATUS_OPEN if not item.get("done") else "done"
    )
    if status != TODO_STATUS_OPEN:
        return -1000.0, ["not_open"], [f"status:{status}"]
    if not todo_item_is_actionable_open(dict(item)):
        score -= 80.0
        hazards.append("not_actionable_open")
    else:
        reason_codes.append("actionable_open")

    priority_rank = todo_priority_rank(dict(item))
    score += max(0, 80 - priority_rank * 12)
    reason_codes.append(f"priority_rank:{priority_rank}")

    task_class = todo_item_task_class(dict(item))
    if task_class == TODO_TASK_CLASS_ADVANCEMENT:
        score += 18.0
        reason_codes.append("advancement_task")
    elif task_class == TODO_TASK_CLASS_MONITOR:
        score -= 20.0
        hazards.append("monitor_lane")
    else:
        score -= 35.0
        hazards.append(f"non_advancement:{task_class}")

    normalized_agent = normalize_todo_claimed_by(agent_id)
    claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
    if normalized_agent and claimed_by == normalized_agent:
        score += 15.0
        reason_codes.append("claimed_by_current_agent")
    elif claimed_by:
        score -= 120.0
        hazards.append(f"claimed_by_other:{claimed_by}")
    else:
        score += 8.0
        reason_codes.append("unclaimed")

    scopes = _required_write_scopes(item)
    if scopes:
        score += 5.0
        reason_codes.append("declared_write_scope")
    else:
        reason_codes.append("unscoped_treat_as_speculative_read_or_coordination")

    capabilities = _required_capabilities(item)
    if capabilities:
        reason_codes.append("declared_capabilities")

    text_tokens = _tokenize(item.get("text")) | _tokenize(item.get("title"))
    overlap = sorted(text_tokens & frontier)
    if overlap:
        score += min(12.0, 2.5 * len(overlap))
        reason_codes.append("explore_frontier_overlap")

    return score, reason_codes, hazards


def _branch_confidence(score: float, *, hazards: Sequence[str] = ()) -> float:
    base = max(0.05, min(0.95, score / 120.0))
    if hazards:
        base *= 0.82
    return round(max(0.05, min(0.95, base)), 3)


def _branch_expected_evidence_units(
    *,
    task_class: str,
    reason_codes: Sequence[str],
    hazards: Sequence[str],
) -> float:
    units = 1.0
    if task_class == TODO_TASK_CLASS_ADVANCEMENT:
        units += 0.35
    elif task_class == TODO_TASK_CLASS_MONITOR:
        units -= 0.45
    if "claimed_by_current_agent" in reason_codes:
        units += 0.1
    if "declared_write_scope" in reason_codes:
        units += 0.1
    if "declared_capabilities" in reason_codes:
        units += 0.05
    if "explore_frontier_overlap" in reason_codes:
        units += 0.25
    if hazards:
        units -= min(0.45, 0.12 * len(hazards))
    return round(max(0.1, units), 3)


def _claim_bucket(item: Mapping[str, Any], *, agent_id: str | None) -> int:
    normalized_agent = normalize_todo_claimed_by(agent_id)
    claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
    if normalized_agent and claimed_by == normalized_agent:
        return 0
    if not claimed_by:
        return 1
    return 2


def _candidate_readiness(item: Mapping[str, Any]) -> dict[str, Any]:
    # Reuse the canonical read model; a planner does not resolve dependencies
    # from ownership, lineage, or the other candidates selected in this batch.
    return {
        "actionable_open": todo_item_is_actionable_open(dict(item)),
        **{key: item[key] for key in ("status", "resume_when", "resume_ready") if key in item},
    }


def _execution_exclusion(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    if candidate.get("task_class") == TODO_TASK_CLASS_MONITOR:
        return {
            **candidate,
            "selection_status": "excluded_non_exploration_lane",
            "exclusion_reason": "continuous_monitor_does_not_consume_exploration_budget",
        }
    if not candidate["actionable_open"]:
        return {**candidate, "selection_status": "not_actionable_open"}
    return None


def _lane_id(todo_id: str, *, index: int) -> str:
    return f"branch_{index}_{todo_id.removeprefix('todo_')}"


def _lease_command(
    *,
    goal_id: str,
    todo_id: str,
    agent_id: str | None,
    write_scopes: Sequence[str],
) -> str | None:
    owner = normalize_todo_claimed_by(agent_id)
    if not owner:
        return None
    parts = [
        "loopx task-lease acquire",
        f"--goal-id {goal_id}",
        f"--todo-id {todo_id}",
        f"--owner {owner}",
        f"--idempotency-key branch-plan:{goal_id}:{todo_id}",
    ]
    for scope in write_scopes:
        parts.append(f"--write-scope {scope}")
    return " ".join(parts)


def _claim_command(*, goal_id: str, todo_id: str, agent_id: str | None) -> str | None:
    owner = normalize_todo_claimed_by(agent_id)
    if not owner:
        return None
    return (
        f"loopx todo claim --goal-id {goal_id} --todo-id {todo_id} "
        f"--claimed-by {owner} --agent-id {owner}"
    )


def resolve_todo_branch_plan_gate(
    orchestration: Mapping[str, Any] | None,
    *,
    requested_width: int,
) -> dict[str, Any]:
    """Resolve the shared explore-harness gate with this planner's lane ceiling."""

    return _resolve_explore_harness_gate(
        orchestration,
        requested_width=requested_width,
        max_lanes=MAX_BRANCH_WIDTH,
        max_lanes_label="max_branch_width",
    )


def _disabled_todo_branch_plan(
    *,
    goal_id: str,
    agent_id: str | None,
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": TODO_BRANCH_PLAN_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": normalize_todo_claimed_by(agent_id) or "",
        "experimental": True,
        "dry_run": True,
        "enabled": False,
        "strategy": "explore_harness_disabled",
        "orchestration_gate": dict(gate),
        "required_contract": explore_harness_required_contract(default_profile="generic"),
        "issue_width": 0,
        "candidate_count": 0,
        "exploration_candidate_count": 0,
        "selected_count": 0,
        "selected_branches": [],
        "rejected_candidates": [],
        "accept_reject_trace": [],
        "boundary": {
            "writes_state": False,
            "claims_todos": False,
            "acquires_leases": False,
            "starts_agents": False,
            "changes_quota": False,
        },
        "next_action": (
            "Todo-branch planning is not enabled for this goal. Opt in by setting "
            "explore_harness.enabled=true on the registered goal's spawn_policy "
            "(projected into quota should-run as goal_boundary.orchestration), then "
            "rerun todo-branch-plan; execution always stays in the normal LoopX "
            "quota/claim/lease lifecycle."
        ),
    }


def build_explore_todo_branch_plan(
    *,
    goal_id: str,
    todos: Sequence[Mapping[str, Any]],
    projection: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    orchestration: Mapping[str, Any] | None = None,
    width: int = DEFAULT_BRANCH_WIDTH,
    allow_unscoped_parallel: bool = True,
    scheduler_strategy: str = "dspark",
    scheduler_load: float = 0.2,
    resource_capacities: Mapping[str, int] | None = None,
    resource_usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Rank open agent todos as CPU-like predicted execution branches.

    This is deliberately a read-only exploration harness. It predicts lanes and
    emits optional claim/lease commands, but it does not mutate active state or
    start worker processes.

    ``orchestration`` is the registered goal's ``spawn_policy`` -- the single
    source projected into ``goal_boundary.orchestration``. Planning is gated on
    ``explore_harness.enabled`` (default deny, including when no boundary is
    provided), suggested commands require ``spawn_allowed``, and issue width
    is capped by ``max_children`` in addition to ``MAX_BRANCH_WIDTH``.
    """

    normalized_agent = normalize_todo_claimed_by(agent_id)
    resource_portfolio = resolve_resource_portfolio(resource_capacities, resource_usage)
    requested_width = max(1, int(width or DEFAULT_BRANCH_WIDTH))
    gate = resolve_todo_branch_plan_gate(orchestration, requested_width=requested_width)
    if gate["state"] == GATE_STATE_DISABLED:
        return _disabled_todo_branch_plan(goal_id=goal_id, agent_id=normalized_agent, gate=gate)
    normalized_width = int(gate["effective_width"])
    frontier = _frontier_tokens(projection)

    candidates: list[dict[str, Any]] = []
    for item in todos:
        todo_id = normalize_todo_id(item.get("todo_id"))
        if not todo_id:
            continue
        score, reason_codes, hazards = _branch_score(
            item,
            agent_id=normalized_agent,
            frontier=frontier,
        )
        required_write_scopes = _required_write_scopes(item)
        task_class = todo_item_task_class(dict(item))
        required_capabilities = _required_capabilities(item)
        candidate = {
            **_candidate_readiness(item),
            "todo_id": todo_id,
            "text": _compact_text(item.get("text") or item.get("title")),
            "priority": item.get("priority"),
            "task_class": task_class,
            "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")) or "",
            "required_write_scopes": required_write_scopes,
            "required_capabilities": required_capabilities,
            "shared_dependency_capabilities": _shared_dependency_capabilities(item),
            "resource_lane": resource_lane_from_capabilities(required_capabilities),
            "depends_on": (
                item.get("depends_on")
                or item.get("dependency_todo_ids")
                or item.get("blocked_by_todo_ids")
                or []
            ),
            "score": round(score, 2),
            "confidence": _branch_confidence(score, hazards=hazards),
            "expected_evidence_units": _branch_expected_evidence_units(
                task_class=task_class,
                reason_codes=reason_codes,
                hazards=hazards,
            ),
            "reason_codes": reason_codes,
            "hazards": hazards,
            "claim_bucket": _claim_bucket(item, agent_id=normalized_agent),
            "source_index": item.get("index"),
        }
        typed_evidence_audit = build_todo_typed_evidence_audit(item, projection)
        if typed_evidence_audit is not None:
            candidate["typed_evidence_audit"] = typed_evidence_audit
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            int(item.get("claim_bucket") or 9),
            *todo_projection_sort_key(item),
            str(item.get("todo_id") or ""),
        )
    )

    excluded_candidates = [
        exclusion
        for candidate in candidates
        if (exclusion := _execution_exclusion(candidate)) is not None
    ]
    exploration_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("task_class") != TODO_TASK_CLASS_MONITOR
        if candidate["actionable_open"]
    ]
    schedulable = [
        candidate
        for candidate in exploration_candidates
        if not (
            candidate.get("claimed_by")
            and candidate.get("claimed_by") != normalized_agent
        )
    ]
    scheduler = schedule_confidence_prefix(
        schedulable,
        max_width=normalized_width,
        max_branch_width=MAX_BRANCH_WIDTH,
        load_factor=scheduler_load,
    )
    verification_budget = max(1, int(scheduler.get("selected_prefix_length") or 1))

    selection_budget = normalized_width if resource_portfolio["enabled"] else verification_budget
    ordered_candidates = list(exploration_candidates)
    if resource_portfolio["enabled"]:
        ordered_candidates.sort(key=lambda item: 0 if item.get("resource_lane") else 1)

    invalidated_ids: set[str] = set()
    dependency_rejections: list[dict[str, Any]] = []
    dependency_events: list[dict[str, Any]] = []
    while True:
        selected = []
        selection_rejections: list[dict[str, Any]] = []
        selected_scopes: list[tuple[str, list[str]]] = []
        selected_by_resource_lane: dict[str, int] = {}
        for candidate in ordered_candidates:
            todo_id = str(candidate.get("todo_id") or "")
            if todo_id in invalidated_ids:
                continue
            hazards = list(candidate.get("hazards") or [])
            scopes = _required_write_scopes(candidate)
            resource_lane = str(candidate.get("resource_lane") or "")
            conflict_with = ""
            if scopes:
                for selected_todo_id, existing_scopes in selected_scopes:
                    if _scopes_overlap(scopes, existing_scopes):
                        conflict_with = selected_todo_id
                        hazards.append(f"write_scope_conflict:{selected_todo_id}")
                        break
            elif not allow_unscoped_parallel and selected:
                conflict_with = selected[0]["todo_id"]
                hazards.append("unscoped_parallel_disabled")

            if candidate.get("claimed_by") and candidate.get("claimed_by") != normalized_agent:
                selection_rejections.append(
                    {**candidate, "selection_status": "blocked_claimed_by_other"}
                )
                continue
            if conflict_with:
                selection_rejections.append(
                    {
                        **candidate,
                        "selection_status": "rejected_hazard",
                        "conflict_with": conflict_with,
                        "hazards": hazards,
                    }
                )
                continue
            if resource_portfolio["enabled"] and resource_lane:
                lane = resource_portfolio["lanes"].get(resource_lane)
                if lane is None:
                    selection_rejections.append(
                        {
                            **candidate,
                            "selection_status": "rejected_resource_lane",
                            "hazards": [*hazards, f"resource_capacity_undeclared:{resource_lane}"],
                        }
                    )
                    continue
                if selected_by_resource_lane.get(resource_lane, 0) >= int(
                    lane.get("available_slots") or 0
                ):
                    selection_rejections.append(
                        {
                            **candidate,
                            "selection_status": "resource_lane_capacity_exhausted",
                            "hazards": [*hazards, f"resource_capacity_exhausted:{resource_lane}"],
                        }
                    )
                    continue
            if len(selected) >= selection_budget:
                selection_rejections.append(
                    {**candidate, "selection_status": "outside_verification_budget"}
                )
                continue

            branch_index = len(selected)
            role = "primary" if branch_index == 0 else "speculative"
            selected_in_lane = selected_by_resource_lane.get(resource_lane, 0) + 1
            selected_candidate = {
                **candidate,
                "selection_status": "selected",
                "branch_role": role,
                "branch_index": branch_index,
                "lane_id": _lane_id(todo_id, index=branch_index),
                "commit_policy": (
                    "verify and write back before keeping this branch; discard or convert "
                    "to successor todo if evidence does not advance the exploration"
                ),
                "suggested_commands": [
                    command
                    for command in (
                        _claim_command(goal_id=goal_id, todo_id=todo_id, agent_id=normalized_agent),
                        _lease_command(
                            goal_id=goal_id,
                            todo_id=todo_id,
                            agent_id=normalized_agent,
                            write_scopes=scopes,
                        ),
                    )
                    if command
                ],
            }
            assignment = resource_assignment(
                resource_portfolio,
                resource_lane=resource_lane,
                selected_count=selected_in_lane,
            )
            if assignment is not None:
                selected_candidate["resource_assignment"] = assignment
            selected.append(selected_candidate)
            selected_scopes.append((todo_id, scopes))
            if resource_lane:
                selected_by_resource_lane[resource_lane] = selected_in_lane

        valid_selected, invalidated, events = partition_invalidated_successors(
            selected,
            selected_ids=[str(item.get("todo_id") or "") for item in selected],
            lane="todo_branch_plan",
        )
        if not invalidated:
            rejected = [*excluded_candidates, *dependency_rejections, *selection_rejections]
            selected = valid_selected
            break
        new_invalidated_ids = {
            str(item.get("todo_id") or "") for item in invalidated
        } - invalidated_ids
        dependency_rejections.extend(invalidated)
        dependency_events.extend(events)
        invalidated_ids.update(new_invalidated_ids)
        if not new_invalidated_ids:
            rejected = [*excluded_candidates, *dependency_rejections, *selection_rejections]
            selected = valid_selected
            break

    if gate["state"] == GATE_STATE_ANALYSIS_ONLY:
        # Analysis stays available (ranking, hazards, A/B estimate), but the
        # boundary forbids spawning workers, so no claim/lease commands leave
        # the planner in any branch list.
        for branch in (*selected, *rejected):
            if branch.get("suggested_commands"):
                branch["suggested_commands"] = []
                branch["commands_suppressed_reason"] = str(gate.get("reason") or "")

    return {
        "ok": True,
        "schema_version": TODO_BRANCH_PLAN_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": normalized_agent or "",
        "experimental": True,
        "dry_run": True,
        "enabled": True,
        "orchestration_gate": gate,
        "strategy": "dspark_confidence_scheduled_todo_branch_prediction",
        "issue_width": normalized_width,
        "requested_issue_width": requested_width,
        "verification_budget": verification_budget,
        "selection_budget": selection_budget,
        "scheduler": scheduler,
        "accept_reject_trace": dependency_events,
        "ab_result": build_branch_plan_ab_result(
            scheduler=scheduler,
            selected=selected,
            rejected=rejected,
        ),
        "allow_unscoped_parallel": bool(allow_unscoped_parallel),
        "source_todo_count": len(todos),
        "candidate_count": len(candidates),
        "exploration_candidate_count": len(exploration_candidates),
        "selected_count": len(selected),
        "selected_branches": selected,
        "rejected_candidate_count": len(rejected),
        "rejected_candidates": rejected[: max(0, normalized_width * 3)],
        "resource_portfolio": resource_portfolio_with_selection(resource_portfolio, selected),
        "hazard_model": {
            "write_scope_conflicts": "skip speculative branch when declared write scopes overlap",
            "shared_dependencies": (
                "shared_artifact:* and shared_implementation:* capabilities are read-only "
                "dependency labels, not write mutexes; mutable shared builds must remain in "
                "required_write_scopes"
            ),
            "claimed_by_other": "other-agent ownership is visible but not selected",
            "monitor_lanes": (
                "continuous_monitor todos stay visible as excluded diagnostics but never "
                "enter the exploration scheduler or consume branch width"
            ),
            "unscoped": (
                "treated as speculative read-or-coordination work by default; disable with "
                "--no-allow-unscoped-parallel"
            ),
            "typed_evidence": (
                "explicit Explore result-node links add bounded diagnostic-only "
                "dead-end/refutation warnings; they do not change score or authority"
            ),
            "resource_lanes": (
                "resource_lane:<key> capabilities consume only explicitly declared empty slots; "
                "rejected candidates release their predicted slot for same-call backfill"
            ),
        },
        "boundary": {
            "writes_state": False,
            "claims_todos": False,
            "acquires_leases": False,
            "starts_agents": False,
            "changes_quota": False,
        },
        "next_action": (
            "Read-only analysis: this goal's orchestration boundary does not permit "
            "spawning workers, so no claim/lease commands are suggested. Update the "
            "registered goal's spawn_policy (spawn_allowed, max_children) to receive "
            "suggested commands; execution stays in the normal LoopX lifecycle."
            if gate["state"] == GATE_STATE_ANALYSIS_ONLY
            else "No actionable branch was selected. Inspect rejected candidates and their "
            "resume conditions; replan when readiness changes."
            if not selected
            else "Run the primary branch normally; hand selected speculative branches to side "
            "agents only after claiming/lease commands are accepted and workspace guards pass."
            if len(selected) > 1
            else "Only one safe branch was selected; keep normal single-todo execution."
        ),
    }
