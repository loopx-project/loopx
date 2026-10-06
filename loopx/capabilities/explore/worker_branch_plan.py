from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping, Sequence

from ...control_plane.todos.contract import (
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from .harness_gate import (
    GATE_STATE_ANALYSIS_ONLY,
    GATE_STATE_DISABLED,
    explore_harness_required_contract,
    resolve_explore_harness_gate as _resolve_explore_harness_gate,
)
from .router_state import family_routing_terms, is_router_state
from .resource_portfolio import (
    resource_assignment,
    resource_lane_from_capabilities,
    resource_portfolio_with_selection,
    resolve_resource_portfolio,
)
from .speculative_scheduler import (
    build_accept_reject_event,
    calibrate_load_factor,
    confident_bundle_prefix_length,
    partition_invalidated_successors,
    schedule_confidence_prefix,
    schedule_independent_lanes,
)
from .todo_branch_plan import (
    DEFAULT_BRANCH_WIDTH,
    MAX_BRANCH_WIDTH,
    TOKEN_PATTERN,
    _branch_confidence,
    _branch_expected_evidence_units,
    _branch_score,
    _claim_command,
    _compact_text,
    _frontier_tokens,
    _lease_command,
    _monitor_lane_exclusion,
    _required_capabilities,
    _required_write_scopes,
    _shared_dependency_capabilities,
    _scopes_overlap,
)
from ...control_plane.todos.todo_semantics import todo_item_task_class, todo_projection_sort_key


WORKER_BRANCH_PLAN_SCHEMA_VERSION = "loopx_explore_worker_branch_plan_v0"
WORKER_HARNESS_PROFILE_SCHEMA_VERSION = "loopx_explore_worker_harness_profile_v0"
DEFAULT_TODOS_PER_WORKER_BRANCH = 3
MAX_TODOS_PER_WORKER_BRANCH = 8
# Worker lanes are independent parallel worker processes; their width ceiling
# must not inherit MAX_BRANCH_WIDTH=8 from the serial todo-branch planner
# (that clamp silently capped a requested width of 10 at 8 in an early
# long-horizon calibration run).
MAX_WORKER_LANES = 16
DEFAULT_WORKER_HARNESS_PROFILE = "generic"
BRANCH_FILL_POLICIES = {"bundle-by-affinity", "value-first", "confident-prefix"}

WORKER_HARNESS_PROFILES: dict[str, dict[str, Any]] = {
    "generic": {
        "schema_version": WORKER_HARNESS_PROFILE_SCHEMA_VERSION,
        "profile": "generic",
        "source": "loopx_explore_worker_branch_plan_default",
        "design_intent": "Generic dry-run worker-lane planning without runtime policy assumptions.",
        "branch_fill_policy": "bundle-by-affinity",
        "concurrency_policy": {
            "mode": "bounded_confidence_prefix",
            "fixed_worker_count": False,
            "does_not_force_requested_width": True,
        },
        "duration_guard": {
            "enabled": False,
            "controlled_by_planner": False,
        },
        "retry_policy": {
            "enabled": False,
            "controlled_by_planner": False,
        },
        "infra_cooldown": {
            "enabled": False,
            "controlled_by_planner": False,
        },
        "todo_batching_policy": {
            "mode": "fixed_safety_ceiling",
            "default_ceiling": DEFAULT_TODOS_PER_WORKER_BRANCH,
            "external_ceiling_role": "planner_batch_ceiling",
        },
    },
    "adaptive-resilient": {
        "schema_version": WORKER_HARNESS_PROFILE_SCHEMA_VERSION,
        "profile": "adaptive-resilient",
        "source": "generalized_from_long_horizon_calibration_evidence",
        "design_intent": (
            "Generalize the best observed harness traits without copying an experimental "
            "N value, duration limit, or saturated-fill policy. Lane width uses "
            "independent-lane admission -- the serial survival-product prefix was a "
            "structural mismatch for parallel lanes and under-filled requested width "
            "in early calibration."
        ),
        "branch_fill_policy": "value-first",
        "marginal_score_floor": 0.72,
        "todo_batching_policy": {
            "mode": "adaptive_value_scheduled",
            "default_ceiling": MAX_TODOS_PER_WORKER_BRANCH,
            "external_ceiling_required": False,
            "external_ceiling_role": "optional_safety_cap_only",
            "intent": (
                "Let the planner decide whether to bundle extra todos into a worker lane "
                "from marginal value, rather than forcing a per-worker todo count."
            ),
        },
        "concurrency_policy": {
            "mode": "independent_lane_admission",
            "fixed_worker_count": False,
            "does_not_force_requested_width": True,
            "worker_width_is_ceiling": True,
            "branch_count_is_value_scheduled": True,
        },
        "lane_start_stagger_seconds": 1.25,
        "retry_policy": {
            "enabled": True,
            "uses_backoff": True,
            "retryable_error_families": [
                "provider_service_unreachable",
                "transient_infra_failure",
            ],
        },
        "infra_cooldown": {
            "enabled": True,
            "failure_threshold": 2,
            "cooldown_epochs": 2,
            "strict_skip_when_healthy_width_available": True,
        },
        "duration_guard": {
            "enabled": False,
            "controlled_by_planner": False,
            "reason": "Duration limits are runner policy, not part of the generalized harness design.",
        },
        "coverage_floor": {
            "enabled": False,
            "reason": "An earlier coverage-floor calibration arm did not show enough value to become the default design.",
        },
    },
    "moe-router": {
        "schema_version": WORKER_HARNESS_PROFILE_SCHEMA_VERSION,
        "profile": "moe-router",
        "source": "moe_style_router_redesign",
        "design_intent": (
            "MoE-style value-first routing under a fixed worker ceiling: task families "
            "are the experts, todos are routed tokens. Width via independent-lane "
            "admission (interference-priced, never a fill rule); coverage via an "
            "aux-loss-free per-family bias that only reorders routing (DeepSeek-V3 "
            "invariant: bias never touches value bookkeeping); bundle length via the "
            "DSpark confident-prefix cutoff (arXiv:2607.05147) with per-family "
            "accept-rate calibration plus a wall-clock straggler guard."
        ),
        "branch_fill_policy": "confident-prefix",
        "marginal_score_floor": 0.72,
        "bundle_confidence_threshold": 0.6,
        # 2.5 x median permits a 2-deep bundle at uniform measured durations
        # while still fencing slow families; 1.25 would cap everything at 1
        # and make the confident-prefix rule unreachable.
        "bundle_straggler_factor": 2.5,
        "todo_batching_policy": {
            "mode": "confident_prefix_scheduled",
            "default_ceiling": MAX_TODOS_PER_WORKER_BRANCH,
            "external_ceiling_required": False,
            "external_ceiling_role": "optional_safety_cap_only",
            "intent": (
                "DSpark-faithful bundle sizing: append todos while calibrated per-todo "
                "acceptance confidence clears the threshold; truncate at the first "
                "below-threshold todo; cap the serial tail by predicted wall-clock."
            ),
        },
        "concurrency_policy": {
            "mode": "independent_lane_admission",
            "fixed_worker_count": False,
            "does_not_force_requested_width": True,
            "worker_width_is_ceiling": True,
            "branch_count_is_value_scheduled": True,
        },
        "opportunistic_lane_policy": {
            "enabled": True,
            "target_utilization_floor": 0.65,
            "min_lane_value_floor": 0.01,
            "min_lane_value_ratio": 0.2,
            "intent": (
                "Keep the high-confidence core from the theta peak, "
                "then admit additional positive-yield lanes up to a utilization floor "
                "so B does not waste worker capacity after proving active-lane quality."
            ),
        },
        "router_policy": {
            "enabled": True,
            "state_schema": "loopx_explore_router_state_v0",
            "bias_affects_routing_order_only": True,
            "admission_uses_unbiased_value": True,
            "experts": "affinity families (scope:artifacts/<family>) declared via work-item write scopes",
        },
        "lane_start_stagger_seconds": 1.25,
        "retry_policy": {
            "enabled": True,
            "uses_backoff": True,
            "retryable_error_families": [
                "provider_service_unreachable",
                "transient_infra_failure",
            ],
        },
        "infra_cooldown": {
            "enabled": True,
            "failure_threshold": 2,
            "cooldown_epochs": 2,
            "strict_skip_when_healthy_width_available": True,
        },
        "duration_guard": {
            "enabled": False,
            "controlled_by_planner": False,
            "reason": "Duration limits are runner policy, not part of the harness design.",
        },
        "coverage_floor": {
            "enabled": False,
            "reason": "Replaced by the aux-loss-free routing bias plus coverage recency bonus.",
        },
    },
}

COMMON_TOPIC_TOKENS = {
    "add",
    "after",
    "agent",
    "against",
    "and",
    "audit",
    "before",
    "between",
    "branch",
    "build",
    "check",
    "codex",
    "continue",
    "current",
    "deliver",
    "existing",
    "explore",
    "fix",
    "from",
    "goal",
    "inspect",
    "into",
    "latest",
    "loopx",
    "new",
    "next",
    "one",
    "p0",
    "p1",
    "p2",
    "recent",
    "review",
    "run",
    "test",
    "the",
    "then",
    "this",
    "through",
    "todo",
    "update",
    "validate",
    "when",
    "while",
    "with",
    "without",
}


def worker_harness_profile_names() -> tuple[str, ...]:
    return tuple(sorted(WORKER_HARNESS_PROFILES))


def _worker_harness_profile(profile: str | None) -> dict[str, Any]:
    normalized = str(profile or DEFAULT_WORKER_HARNESS_PROFILE).strip().lower().replace("_", "-")
    if normalized not in WORKER_HARNESS_PROFILES:
        choices = ", ".join(worker_harness_profile_names())
        raise ValueError(f"unknown worker harness profile {profile!r}; expected one of: {choices}")
    return deepcopy(WORKER_HARNESS_PROFILES[normalized])


def _normalize_branch_fill_policy(value: str | None, *, profile: Mapping[str, Any]) -> str:
    policy = str(value or profile.get("branch_fill_policy") or "bundle-by-affinity").strip().lower()
    policy = policy.replace("_", "-")
    if policy not in BRANCH_FILL_POLICIES:
        choices = ", ".join(sorted(BRANCH_FILL_POLICIES))
        raise ValueError(f"unknown branch fill policy {value!r}; expected one of: {choices}")
    return policy


def _resolve_todo_bundle_ceiling(
    value: int | None,
    *,
    profile: Mapping[str, Any],
) -> tuple[int, bool, str]:
    """Return the worker-lane bundle ceiling and whether it was explicitly set."""

    explicit = value is not None and int(value or 0) > 0
    if explicit:
        return max(1, min(MAX_TODOS_PER_WORKER_BRANCH, int(value or 1))), True, "explicit_safety_cap"
    batching_policy = profile.get("todo_batching_policy") or {}
    if str(batching_policy.get("mode") or "") == "adaptive_value_scheduled":
        default_ceiling = int(batching_policy.get("default_ceiling") or MAX_TODOS_PER_WORKER_BRANCH)
        return max(1, min(MAX_TODOS_PER_WORKER_BRANCH, default_ceiling)), False, "adaptive_planner_default"
    if str(batching_policy.get("mode") or "") == "confident_prefix_scheduled":
        default_ceiling = int(batching_policy.get("default_ceiling") or MAX_TODOS_PER_WORKER_BRANCH)
        return (
            max(1, min(MAX_TODOS_PER_WORKER_BRANCH, default_ceiling)),
            False,
            "confident_prefix_scheduler_safety_cap",
        )
    default_ceiling = int(batching_policy.get("default_ceiling") or DEFAULT_TODOS_PER_WORKER_BRANCH)
    return max(1, min(MAX_TODOS_PER_WORKER_BRANCH, default_ceiling)), False, "profile_default"


def _ordered_topic_tokens(value: Any) -> list[str]:
    """Return meaningful topic tokens in their original textual order."""

    tokens: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_PATTERN.finditer(str(value or "")):
        token = match.group(0).lower()
        if token in COMMON_TOPIC_TOKENS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _scope_family(scope: str) -> str:
    clean = str(scope or "").replace("\\", "/").strip()
    for marker in ("/**", "/*"):
        if marker in clean:
            clean = clean.split(marker, 1)[0]
    parts = [part for part in clean.split("/") if part and part not in {".", "**", "*"}]
    if len(parts) >= 2:
        return "/".join(parts[:2])
    if parts:
        return parts[0]
    return ""


def _affinity_key(candidate: Mapping[str, Any]) -> str:
    scopes = [scope for scope in candidate.get("required_write_scopes") or [] if str(scope).strip()]
    if scopes:
        family = _scope_family(str(scopes[0]))
        if family:
            return f"scope:{family}"
    capabilities = [
        capability for capability in candidate.get("required_capabilities") or [] if str(capability).strip()
    ]
    if capabilities:
        return f"capability:{str(capabilities[0]).strip()}"
    tokens = _ordered_topic_tokens(candidate.get("text"))
    if tokens:
        return f"topic:{tokens[0]}"
    return "topic:general"


def _claim_bucket(candidate: Mapping[str, Any], *, agent_id: str | None) -> int:
    normalized_agent = normalize_todo_claimed_by(agent_id)
    claimed_by = normalize_todo_claimed_by(candidate.get("claimed_by"))
    if normalized_agent and claimed_by == normalized_agent:
        return 0
    if not claimed_by:
        return 1
    return 2


def _todo_candidate(
    item: Mapping[str, Any],
    *,
    agent_id: str | None,
    frontier: set[str],
) -> dict[str, Any] | None:
    todo_id = normalize_todo_id(item.get("todo_id"))
    if not todo_id:
        return None
    score, reason_codes, hazards = _branch_score(
        item,
        agent_id=agent_id,
        frontier=frontier,
    )
    task_class = todo_item_task_class(dict(item))
    required_capabilities = _required_capabilities(item)
    candidate = {
        "todo_id": todo_id,
        "text": _compact_text(item.get("text") or item.get("title")),
        "priority": item.get("priority"),
        "task_class": task_class,
        "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")) or "",
        "required_write_scopes": _required_write_scopes(item),
        "required_capabilities": required_capabilities,
        "shared_dependency_capabilities": _shared_dependency_capabilities(item),
        "resource_lane": resource_lane_from_capabilities(required_capabilities),
        "depends_on": item.get("depends_on") or item.get("dependency_todo_ids") or item.get("blocked_by_todo_ids") or [],
        "score": round(score, 2),
        "confidence": _branch_confidence(score, hazards=hazards),
        "expected_evidence_units": _branch_expected_evidence_units(
            task_class=task_class,
            reason_codes=reason_codes,
            hazards=hazards,
        ),
        "reason_codes": reason_codes,
        "hazards": hazards,
        "claim_bucket": _claim_bucket(item, agent_id=agent_id),
        "source_index": item.get("index"),
    }
    candidate["affinity_key"] = _affinity_key(candidate)
    return candidate


def _dedupe_preserve_order(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _branch_id(affinity_key: str, *, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", affinity_key).strip("_").lower() or "general"
    return f"worker_branch_{index}_{slug}"


def _aggregate_confidence(bundle: Sequence[Mapping[str, Any]]) -> float:
    if not bundle:
        return 0.0
    confidence = 1.0
    for item in bundle:
        confidence *= max(0.05, min(0.95, float(item.get("confidence") or 0.05)))
    return round(confidence ** (1.0 / len(bundle)), 3)


def _branch_candidate(
    *,
    goal_id: str,
    agent_id: str | None,
    affinity_key: str,
    bundle: Sequence[Mapping[str, Any]],
    index: int,
    router_terms: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    todo_bundle = [dict(item) for item in bundle]
    todo_ids = [str(item["todo_id"]) for item in todo_bundle]
    write_scopes = _dedupe_preserve_order(
        scope for item in todo_bundle for scope in item.get("required_write_scopes") or []
    )
    capabilities = _dedupe_preserve_order(
        capability for item in todo_bundle for capability in item.get("required_capabilities") or []
    )
    shared_dependency_capabilities = _dedupe_preserve_order(
        capability
        for item in todo_bundle
        for capability in item.get("shared_dependency_capabilities") or []
    )
    dependencies = _dedupe_preserve_order(
        dep for item in todo_bundle for dep in item.get("depends_on") or []
    )
    hazards = _dedupe_preserve_order(
        hazard for item in todo_bundle for hazard in item.get("hazards") or []
    )
    branch_id = _branch_id(affinity_key, index=index)
    resource_lanes = _dedupe_preserve_order(
        item.get("resource_lane") for item in todo_bundle if item.get("resource_lane")
    )
    if len(resource_lanes) > 1:
        raise ValueError("a worker branch cannot bundle todos from multiple resource lanes")
    expected_evidence = round(
        sum(float(item.get("expected_evidence_units") or 0.0) for item in todo_bundle),
        3,
    )
    score = round(sum(float(item.get("score") or 0.0) for item in todo_bundle), 2)
    commands: list[str] = []
    for item in todo_bundle:
        todo_id = str(item["todo_id"])
        commands.extend(
            command
            for command in (
                _claim_command(goal_id=goal_id, todo_id=todo_id, agent_id=agent_id),
                _lease_command(
                    goal_id=goal_id,
                    todo_id=todo_id,
                    agent_id=agent_id,
                    write_scopes=item.get("required_write_scopes") or [],
                ),
            )
            if command
        )
    aggregate_confidence = _aggregate_confidence(todo_bundle)
    candidate = {
        "branch_id": branch_id,
        "todo_id": branch_id,
        "affinity_key": affinity_key,
        "objective_slice": affinity_key.removeprefix("scope:").removeprefix("capability:").removeprefix("topic:"),
        "todo_ids": todo_ids,
        "todo_bundle": todo_bundle,
        "required_write_scopes": write_scopes,
        "required_capabilities": capabilities,
        "shared_dependency_capabilities": shared_dependency_capabilities,
        "resource_lane": resource_lanes[0] if resource_lanes else "",
        "depends_on": dependencies,
        "score": score,
        "confidence": aggregate_confidence,
        "expected_evidence_units": expected_evidence,
        "hazards": hazards,
        "suggested_commands": commands,
        "merge_policy": "merge only verified node/edge/finding evidence through LoopX explore result_log",
        "rollback_policy": "record rejected/dead-end evidence; do not mutate active state from the plan packet",
    }
    if router_terms:
        # Bias enters ONLY routing_score (ordering); calibrated confidence and
        # novelty-adjusted evidence stay bias-free so admission and all
        # reported value bookkeeping remain unbiased (aux-loss-free invariant).
        # Zero is a meaningful value for these terms, so guard on None -- an
        # `or`-default would silently turn a fully-depleted/rejected family
        # back into a fresh one.
        multiplier = router_terms.get("routing_multiplier")
        accept_rate = router_terms.get("accept_rate")
        novelty = router_terms.get("novelty")
        candidate["router"] = dict(router_terms)
        candidate["routing_score"] = round(
            score * (float(multiplier) if multiplier is not None else 1.0), 2
        )
        candidate["calibrated_confidence"] = round(
            max(
                0.05,
                min(
                    0.95,
                    aggregate_confidence
                    * (float(accept_rate) if accept_rate is not None else 1.0),
                ),
            ),
            3,
        )
        candidate["novelty_adjusted_evidence_units"] = round(
            expected_evidence * (float(novelty) if novelty is not None else 1.0),
            3,
        )
    return candidate


def _confident_prefix_bundles(
    bucket: Sequence[Mapping[str, Any]],
    *,
    chunk_size: int,
    confidence_threshold: float,
    straggler_factor: float,
    router_terms: Mapping[str, Any] | None,
    median_duration_minutes: float | None,
) -> list[list[dict[str, Any]]]:
    """DSpark-faithful bundle sizing (arXiv:2607.05147).

    A lane's serial todo bundle is the draft block: append todos while the
    calibrated per-todo acceptance confidence (plan confidence x learned
    family accept rate) clears the threshold, truncating at the first
    below-threshold todo. Unlike DSpark -- where drafting is nearly free -- a
    serial todo costs real wall-clock, so a straggler guard caps the bundle
    so its predicted wall stays within ``straggler_factor`` x the median
    single-probe duration. A lane always carries at least its seed todo.
    """

    raw_accept_rate = (router_terms or {}).get("accept_rate")
    accept_rate = float(raw_accept_rate) if raw_accept_rate is not None else 1.0
    family_duration = float((router_terms or {}).get("duration_minutes") or 0.0)
    max_by_wall = chunk_size
    if (
        straggler_factor > 0
        and median_duration_minutes
        and family_duration > 0
        # Only bind the wall cap on MEASURED durations: cold-start defaults are
        # uniform by construction and would silently cap every bundle at the
        # floor, turning the confident-prefix rule into dead code.
        and bool((router_terms or {}).get("duration_observed"))
    ):
        max_by_wall = max(
            1, int((straggler_factor * median_duration_minutes) // max(family_duration, 0.05))
        )
    bundles: list[list[dict[str, Any]]] = []
    remaining = list(bucket)
    while remaining:
        window = remaining[: max(1, chunk_size)]
        confidences = [
            max(0.0, min(1.0, float(item.get("confidence") or 0.0) * accept_rate))
            for item in window
        ]
        prefix = confident_bundle_prefix_length(
            confidences,
            threshold=confidence_threshold,
            max_length=chunk_size,
        )
        length = min(max(1, prefix), max_by_wall)
        bundles.append(remaining[:length])
        remaining = remaining[length:]
    return bundles


def _build_worker_branch_candidates(
    *,
    goal_id: str,
    todos: Sequence[Mapping[str, Any]],
    projection: Mapping[str, Any] | None,
    agent_id: str | None,
    max_todos_per_branch: int,
    branch_fill_policy: str,
    marginal_score_floor: float,
    bundle_confidence_threshold: float = 0.0,
    bundle_straggler_factor: float = 0.0,
    router_state: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_agent = normalize_todo_claimed_by(agent_id)
    frontier = _frontier_tokens(projection)
    todo_candidates = [
        candidate
        for item in todos
        if (candidate := _todo_candidate(item, agent_id=normalized_agent, frontier=frontier))
    ]
    todo_candidates.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            int(item.get("claim_bucket") or 9),
            *todo_projection_sort_key(item),
            str(item.get("todo_id") or ""),
        )
    )

    monitor_lanes = [
        exclusion
        for candidate in todo_candidates
        if (exclusion := _monitor_lane_exclusion(candidate)) is not None
    ]
    blocked = [
        {**candidate, "selection_status": "blocked_claimed_by_other"}
        for candidate in todo_candidates
        if candidate.get("task_class") != TODO_TASK_CLASS_MONITOR
        if candidate.get("claimed_by") and candidate.get("claimed_by") != normalized_agent
    ]
    eligible = [
        candidate
        for candidate in todo_candidates
        if candidate.get("task_class") != TODO_TASK_CLASS_MONITOR
        if not (candidate.get("claimed_by") and candidate.get("claimed_by") != normalized_agent)
    ]

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in eligible:
        bucket_key = (
            str(candidate.get("resource_lane") or ""),
            str(candidate.get("affinity_key") or "topic:general"),
        )
        buckets.setdefault(bucket_key, []).append(candidate)

    router_enabled = is_router_state(router_state)
    terms_by_family: dict[tuple[str, str], dict[str, Any]] = {}
    if router_enabled:
        terms_by_family = {
            bucket_key: family_routing_terms(router_state, bucket_key[1])
            for bucket_key in buckets
        }
    known_durations = sorted(
        terms.get("duration_minutes")
        for terms in terms_by_family.values()
        if terms.get("duration_minutes") and terms.get("duration_observed")
    )
    median_duration = (
        float(known_durations[len(known_durations) // 2]) if known_durations else None
    )

    def _bucket_sort_key(item: tuple[tuple[str, str], list[dict[str, Any]]]) -> tuple:
        bucket_key, bucket = item
        resource_lane, affinity_key = bucket_key
        total_score = sum(float(todo.get("score") or 0.0) for todo in bucket)
        if router_enabled:
            routing_value = float(
                (terms_by_family.get(bucket_key) or {}).get("routing_value") or 0.0
            )
            return (-routing_value, -total_score, resource_lane, affinity_key)
        return (-total_score, resource_lane, affinity_key)

    branch_candidates: list[dict[str, Any]] = []
    branch_index = 0
    chunk_size = max(1, min(MAX_TODOS_PER_WORKER_BRANCH, int(max_todos_per_branch or 1)))
    score_floor = max(0.0, min(1.0, float(marginal_score_floor)))
    for bucket_key, bucket in sorted(buckets.items(), key=_bucket_sort_key):
        _, affinity_key = bucket_key
        router_terms = terms_by_family.get(bucket_key)
        bundles: list[list[dict[str, Any]]] = []
        if branch_fill_policy == "confident-prefix":
            bundles = _confident_prefix_bundles(
                bucket,
                chunk_size=chunk_size,
                confidence_threshold=max(0.0, min(1.0, float(bundle_confidence_threshold))),
                straggler_factor=max(0.0, float(bundle_straggler_factor)),
                router_terms=router_terms,
                median_duration_minutes=median_duration,
            )
        elif branch_fill_policy == "value-first":
            remaining = list(bucket)
            while remaining:
                seed = remaining.pop(0)
                bundle = [seed]
                seed_score = max(0.001, float(seed.get("score") or 0.001))
                while remaining and len(bundle) < chunk_size:
                    next_score = float(remaining[0].get("score") or 0.0)
                    if next_score < seed_score * score_floor:
                        break
                    bundle.append(remaining.pop(0))
                bundles.append(bundle)
        else:
            bundles = [bucket[offset : offset + chunk_size] for offset in range(0, len(bucket), chunk_size)]
        for bundle in bundles:
            branch_index += 1
            branch_candidates.append(
                _branch_candidate(
                    goal_id=goal_id,
                    agent_id=normalized_agent,
                    affinity_key=affinity_key,
                    bundle=bundle,
                    index=branch_index,
                    router_terms=router_terms,
                )
            )

    def _branch_sort_key(item: Mapping[str, Any]) -> tuple:
        if router_enabled:
            primary = -float(item.get("routing_score") or item.get("score") or 0.0)
        else:
            primary = -float(item.get("score") or 0.0)
        return (
            primary,
            -float(item.get("expected_evidence_units") or 0.0),
            str(item.get("branch_id") or ""),
        )

    branch_candidates.sort(key=_branch_sort_key)
    return branch_candidates, [*monitor_lanes, *blocked]


def _baseline_worker_lanes(branch_candidates: Sequence[Mapping[str, Any]], *, width: int) -> dict[str, Any]:
    selected = list(branch_candidates[: max(1, int(width or 1))])
    return {
        "strategy": "loopx_priority_order_worker_lanes",
        "selected_branch_ids": [item.get("branch_id") for item in selected],
        "expected_evidence_units": round(
            sum(float(item.get("expected_evidence_units") or 0.0) for item in selected),
            3,
        ),
        "confidence_mean": round(
            sum(float(item.get("confidence") or 0.0) for item in selected) / max(1, len(selected)),
            3,
        ),
    }


def resolve_explore_harness_gate(
    orchestration: Mapping[str, Any] | None,
    *,
    requested_width: int,
) -> dict[str, Any]:
    """Resolve the shared explore-harness gate with this planner's lane ceiling."""

    return _resolve_explore_harness_gate(
        orchestration,
        requested_width=requested_width,
        max_lanes=MAX_WORKER_LANES,
        max_lanes_label="max_worker_lanes",
    )


def _disabled_worker_branch_plan(
    *,
    goal_id: str,
    agent_id: str | None,
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": WORKER_BRANCH_PLAN_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": normalize_todo_claimed_by(agent_id) or "",
        "experimental": True,
        "dry_run": True,
        "enabled": False,
        "strategy": "explore_harness_disabled",
        "orchestration_gate": dict(gate),
        "required_contract": explore_harness_required_contract(
            default_profile=DEFAULT_WORKER_HARNESS_PROFILE
        ),
        "worker_width": 0,
        "max_worker_lanes": MAX_WORKER_LANES,
        "branch_candidate_count": 0,
        "excluded_monitor_todo_count": 0,
        "selected_worker_branch_count": 0,
        "selected_worker_branches": [],
        "rejected_worker_branches": [],
        "accept_reject_trace": [],
        "boundary": {
            "writes_state": False,
            "claims_todos": False,
            "acquires_leases": False,
            "starts_agents": False,
            "changes_quota": False,
        },
        "next_action": (
            "Worker-lane planning is not enabled for this goal. Opt in by setting "
            "explore_harness.enabled=true on the registered goal's spawn_policy "
            "(projected into quota should-run as goal_boundary.orchestration), then "
            "rerun worker-branch-plan; execution always stays in the normal LoopX "
            "quota/claim/lease lifecycle."
        ),
    }


def build_explore_worker_branch_plan(
    *,
    goal_id: str,
    todos: Sequence[Mapping[str, Any]],
    projection: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    orchestration: Mapping[str, Any] | None = None,
    worker_width: int = DEFAULT_BRANCH_WIDTH,
    max_todos_per_branch: int | None = None,
    scheduler_load: float = 0.2,
    allow_unscoped_parallel: bool = True,
    harness_profile: str = DEFAULT_WORKER_HARNESS_PROFILE,
    branch_fill_policy: str | None = None,
    marginal_score_floor: float | None = None,
    router_state: Mapping[str, Any] | None = None,
    load_profile: Mapping[str, Any] | None = None,
    resource_capacities: Mapping[str, int] | None = None,
    resource_usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Plan worker-lane branches on top of the existing LoopX harness.

    The packet is deliberately read-only: it consumes LoopX todo/projection
    state and emits worker-lane bundles plus suggested claim/lease commands,
    but it does not launch workers or mutate quota, leases, todos, or state.

    ``orchestration`` is the registered goal's ``spawn_policy`` -- the single
    source projected into ``goal_boundary.orchestration``. Planning is gated on
    ``explore_harness.enabled`` (default deny, including when no boundary is
    provided), suggested commands require ``spawn_allowed``, and worker width
    is capped by ``max_children`` in addition to ``MAX_WORKER_LANES``.

    ``router_state`` (see ``router_state.py``) carries cross-epoch learned
    per-family statistics maintained by the runner; it is consumed only by
    profiles whose ``router_policy`` enables it. ``load_profile`` carries the
    runner's observed parallel timings for ``calibrate_load_factor`` so lane
    admission prices measured interference instead of a hardcoded prior.
    """

    normalized_agent = normalize_todo_claimed_by(agent_id)
    resource_portfolio = resolve_resource_portfolio(resource_capacities, resource_usage)
    requested_width = max(1, int(worker_width or DEFAULT_BRANCH_WIDTH))
    gate = resolve_explore_harness_gate(orchestration, requested_width=requested_width)
    if gate["state"] == GATE_STATE_DISABLED:
        return _disabled_worker_branch_plan(goal_id=goal_id, agent_id=normalized_agent, gate=gate)
    pinned_profile = gate.get("goal_pinned_profile")
    profile = _worker_harness_profile(str(pinned_profile) if pinned_profile else harness_profile)
    gate["requested_profile"] = str(harness_profile or DEFAULT_WORKER_HARNESS_PROFILE)
    gate["effective_profile"] = profile.get("profile")
    gate["profile_source"] = "goal_boundary" if pinned_profile else "planner_request"
    normalized_fill_policy = _normalize_branch_fill_policy(branch_fill_policy, profile=profile)
    normalized_marginal_floor = (
        float(marginal_score_floor)
        if marginal_score_floor is not None
        else float(profile.get("marginal_score_floor") or 0.0)
    )
    normalized_width = int(gate["effective_width"])

    router_policy = profile.get("router_policy") or {}
    router_supported = bool(router_policy.get("enabled"))
    router_provided = is_router_state(router_state)
    router_used = router_supported and router_provided

    load_calibration: dict[str, Any] | None = None
    effective_load = max(0.0, min(1.0, float(scheduler_load)))
    if load_profile:
        load_calibration = calibrate_load_factor(
            load_profile,
            fallback_load_factor=effective_load,
        )
        effective_load = float(load_calibration.get("load_factor") or effective_load)

    normalized_max_todos, max_todos_explicit, max_todos_source = _resolve_todo_bundle_ceiling(
        max_todos_per_branch,
        profile=profile,
    )
    branch_candidates, blocked_todos = _build_worker_branch_candidates(
        goal_id=goal_id,
        todos=todos,
        projection=projection,
        agent_id=normalized_agent,
        max_todos_per_branch=normalized_max_todos,
        branch_fill_policy=normalized_fill_policy,
        marginal_score_floor=normalized_marginal_floor,
        bundle_confidence_threshold=float(profile.get("bundle_confidence_threshold") or 0.0),
        bundle_straggler_factor=float(profile.get("bundle_straggler_factor") or 0.0),
        router_state=router_state if router_used else None,
    )
    excluded_monitor_todo_count = sum(
        item.get("selection_status") == "excluded_non_exploration_lane"
        for item in blocked_todos
    )
    if gate["state"] == GATE_STATE_ANALYSIS_ONLY:
        # Analysis stays available (ranking, bundles, A/B estimate), but the
        # boundary forbids spawning workers, so no claim/lease commands leave
        # the planner in any branch list.
        for branch in branch_candidates:
            branch["suggested_commands"] = []
            branch["commands_suppressed_reason"] = str(gate.get("reason") or "")
    concurrency_mode = str((profile.get("concurrency_policy") or {}).get("mode") or "")
    if concurrency_mode == "independent_lane_admission":
        opportunistic_policy = profile.get("opportunistic_lane_policy") or {}
        # Admission consumes UNBIASED value terms (calibrated confidence and
        # novelty-adjusted evidence carry no routing bias); the bias already
        # did its only job upstream by reordering branch_candidates.
        scheduler_view = [
            {
                "todo_id": branch.get("branch_id"),
                "confidence": branch.get("calibrated_confidence", branch.get("confidence")),
                "expected_evidence_units": branch.get(
                    "novelty_adjusted_evidence_units", branch.get("expected_evidence_units")
                ),
            }
            for branch in branch_candidates
        ]
        scheduler = schedule_independent_lanes(
            scheduler_view,
            max_width=normalized_width,
            load_factor=effective_load,
            opportunistic_utilization_floor=(
                float(opportunistic_policy.get("target_utilization_floor") or 0.0)
                if opportunistic_policy.get("enabled")
                else 0.0
            ),
            opportunistic_lane_value_floor=(
                float(opportunistic_policy.get("min_lane_value_floor") or 0.0)
                if opportunistic_policy.get("enabled")
                else 0.0
            ),
            opportunistic_lane_value_ratio=(
                float(opportunistic_policy.get("min_lane_value_ratio") or 0.0)
                if opportunistic_policy.get("enabled")
                else 0.0
            ),
        )
    else:
        scheduler = schedule_confidence_prefix(
            branch_candidates,
            max_width=normalized_width,
            max_branch_width=MAX_BRANCH_WIDTH,
            load_factor=effective_load,
        )
    verification_budget = max(1, int(scheduler.get("selected_prefix_length") or 1))
    profile["branch_fill_policy"] = normalized_fill_policy
    profile["marginal_score_floor"] = round(max(0.0, min(1.0, normalized_marginal_floor)), 3)
    profile["worker_width_ceiling"] = normalized_width
    profile["max_todos_per_branch_ceiling"] = normalized_max_todos
    profile["max_todos_per_branch_ceiling_explicit"] = max_todos_explicit
    profile["max_todos_per_branch_ceiling_source"] = max_todos_source
    profile["scheduler_load"] = scheduler.get("load_factor")
    profile["scheduler_model"] = scheduler.get("strategy")

    selection_budget = normalized_width if resource_portfolio["enabled"] else verification_budget
    ordered_branches = list(branch_candidates)
    if resource_portfolio["enabled"]:
        ordered_branches.sort(key=lambda item: 0 if item.get("resource_lane") else 1)

    invalidated_branch_ids: set[str] = set()
    dependency_rejections: list[dict[str, Any]] = []
    invalidation_events: list[dict[str, Any]] = []
    while True:
        selected = []
        selection_rejections: list[dict[str, Any]] = []
        selected_scopes: list[tuple[str, list[str]]] = []
        selected_by_resource_lane: dict[str, int] = {}
        for branch in ordered_branches:
            branch_id = str(branch.get("branch_id") or "")
            if branch_id in invalidated_branch_ids:
                continue
            branch_scopes = list(branch.get("required_write_scopes") or [])
            resource_lane = str(branch.get("resource_lane") or "")
            conflict_with = ""
            if branch_scopes:
                for selected_branch_id, selected_branch_scopes in selected_scopes:
                    if _scopes_overlap(branch_scopes, selected_branch_scopes):
                        conflict_with = selected_branch_id
                        break
            elif not allow_unscoped_parallel and selected:
                conflict_with = str(selected[0].get("branch_id") or "")

            if conflict_with:
                selection_rejections.append(
                    {
                        **branch,
                        "selection_status": "rejected_hazard",
                        "conflict_with": conflict_with,
                        "hazards": [
                            *list(branch.get("hazards") or []),
                            f"worker_branch_conflict:{conflict_with}",
                        ],
                    }
                )
                continue
            if resource_portfolio["enabled"] and resource_lane:
                lane = resource_portfolio["lanes"].get(resource_lane)
                if lane is None:
                    selection_rejections.append(
                        {
                            **branch,
                            "selection_status": "rejected_resource_lane",
                            "hazards": [
                                *list(branch.get("hazards") or []),
                                f"resource_capacity_undeclared:{resource_lane}",
                            ],
                        }
                    )
                    continue
                if selected_by_resource_lane.get(resource_lane, 0) >= int(
                    lane.get("available_slots") or 0
                ):
                    selection_rejections.append(
                        {
                            **branch,
                            "selection_status": "resource_lane_capacity_exhausted",
                            "hazards": [
                                *list(branch.get("hazards") or []),
                                f"resource_capacity_exhausted:{resource_lane}",
                            ],
                        }
                    )
                    continue
            if len(selected) >= selection_budget:
                selection_rejections.append(
                    {**branch, "selection_status": "outside_verification_budget"}
                )
                continue

            role = "primary" if not selected else "speculative_worker"
            selected_in_lane = selected_by_resource_lane.get(resource_lane, 0) + 1
            selected_branch = {
                **branch,
                "selection_status": "selected",
                "branch_role": role,
                "branch_index": len(selected),
                "worker_hint": f"worker-{len(selected) + 1}",
                "execution_contract": {
                    "must_enter_loopx_harness": True,
                    "requires_quota_should_run": True,
                    "requires_todo_claim": True,
                    "requires_task_lease_for_write_scopes": bool(branch_scopes),
                    "writeback_surfaces": ["explore_result_log", "refresh_state", "quota_spend_slot"],
                },
            }
            assignment = resource_assignment(
                resource_portfolio,
                resource_lane=resource_lane,
                selected_count=selected_in_lane,
            )
            if assignment is not None:
                selected_branch["resource_assignment"] = assignment
            selected.append(selected_branch)
            selected_scopes.append((branch_id, branch_scopes))
            if resource_lane:
                selected_by_resource_lane[resource_lane] = selected_in_lane

        valid_selected, invalidated, events = partition_invalidated_successors(
            selected,
            selected_ids=[
                todo_id
                for selected_branch in selected
                for todo_id in selected_branch.get("todo_ids") or []
            ],
            lane="worker_branch_plan",
        )
        if not invalidated:
            rejected = [*blocked_todos, *dependency_rejections, *selection_rejections]
            selected = valid_selected
            break
        new_invalidated_ids = {
            str(item.get("branch_id") or "") for item in invalidated
        } - invalidated_branch_ids
        dependency_rejections.extend(invalidated)
        invalidation_events.extend(events)
        invalidated_branch_ids.update(new_invalidated_ids)
        if not new_invalidated_ids:
            rejected = [*blocked_todos, *dependency_rejections, *selection_rejections]
            selected = valid_selected
            break

    baseline = _baseline_worker_lanes(branch_candidates, width=normalized_width)
    treatment_expected = round(
        sum(float(branch.get("expected_evidence_units") or 0.0) for branch in selected),
        3,
    )
    baseline_expected = max(0.001, float(baseline.get("expected_evidence_units") or 0.0))
    predicted_events = [
        build_accept_reject_event(
            lane="worker_branch_plan",
            event_type="predicted",
            todo_id=str(branch.get("branch_id") or ""),
            reason="worker_lane_selected_for_harness_execution",
            payload={
                "todo_ids": branch.get("todo_ids") or [],
                "worker_hint": branch.get("worker_hint"),
                "confidence": branch.get("confidence"),
                "expected_evidence_units": branch.get("expected_evidence_units"),
            },
        )
        for branch in selected
    ]
    return {
        "ok": True,
        "schema_version": WORKER_BRANCH_PLAN_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": normalized_agent or "",
        "experimental": True,
        "dry_run": True,
        "enabled": True,
        "orchestration_gate": gate,
        "strategy": (
            "moe_router_worker_lane_prediction"
            if router_used
            else "independent_lane_worker_prediction"
            if concurrency_mode == "independent_lane_admission"
            else "dspark_worker_lane_prediction"
        ),
        "harness_profile": profile.get("profile"),
        "worker_harness_profile": profile,
        "worker_width": normalized_width,
        "requested_worker_width": requested_width,
        "max_worker_lanes": MAX_WORKER_LANES,
        "scheduler_model": scheduler.get("strategy"),
        "load_calibration": load_calibration,
        "router": {
            "enabled": router_used,
            "supported_by_profile": router_supported,
            "state_provided": router_provided,
            "state_schema_version": (router_state or {}).get("schema_version") if router_provided else None,
            "state_updated_epoch": (router_state or {}).get("updated_epoch") if router_provided else None,
            "known_family_count": len((router_state or {}).get("families") or {}) if router_provided else 0,
            "bias_affects_routing_order_only": True,
        },
        "admission_audit": scheduler.get("admission_audit"),
        "max_todos_per_branch": normalized_max_todos,
        "max_todos_per_branch_explicit": max_todos_explicit,
        "max_todos_per_branch_source": max_todos_source,
        "branch_fill_policy": normalized_fill_policy,
        "verification_budget": verification_budget,
        "selection_budget": selection_budget,
        "scheduler": scheduler,
        "harness_compatibility": {
            "uses_loopx_todo_projection": True,
            "uses_explore_projection": True,
            "gated_by_goal_boundary_orchestration": True,
            "replaces_loopx_runtime": False,
            "launches_workers": False,
            "mutates_quota_or_state": False,
            "claim_and_lease_are_suggested_only": True,
            "duration_guard_controlled_by_planner": False,
            "fixed_worker_count_controlled_by_planner": False,
            "forces_full_branch_fill": False,
            "todo_bundle_ceiling_is_safety_cap": max_todos_source in {
                "explicit_safety_cap",
                "adaptive_planner_default",
                "confident_prefix_scheduler_safety_cap",
            },
            "adaptive_todo_batching": max_todos_source in {
                "adaptive_planner_default",
                "confident_prefix_scheduler_safety_cap",
            },
            "confidence_prefix_todo_batching": (
                max_todos_source == "confident_prefix_scheduler_safety_cap"
            ),
        },
        "ab_result": {
            "schema_version": "loopx_explore_worker_branch_plan_ab_result_v0",
            "metric": "estimated_worker_lane_evidence_units",
            "baseline_strategy": baseline["strategy"],
            "treatment_strategy": "adaptive_worker_branch_plan_concurrent",
            "harness_profile": profile.get("profile"),
            "baseline_selected_branch_ids": baseline["selected_branch_ids"],
            "baseline_expected_evidence_units": baseline["expected_evidence_units"],
            "dspark_selected_branch_ids": [branch.get("branch_id") for branch in selected],
            "dspark_expected_evidence_units": treatment_expected,
            "estimated_speedup_vs_baseline": round(treatment_expected / baseline_expected, 3),
            "interpretation": (
                "Dry-run only: compares LoopX priority-order worker lanes with "
                "DSpark-style worker-lane bundles before any worker is launched."
            ),
        },
        "branch_candidate_count": len(branch_candidates),
        "excluded_monitor_todo_count": excluded_monitor_todo_count,
        "selected_worker_branch_count": len(selected),
        "selected_worker_branches": selected,
        "rejected_worker_branches": rejected[: max(0, normalized_width * 3)],
        "resource_portfolio": resource_portfolio_with_selection(resource_portfolio, selected),
        "accept_reject_trace": [*predicted_events, *invalidation_events],
        "execution_model": {
            "planner_layer": "experimental worker-branch planner",
            "execution_layer": "existing LoopX harness",
            "worker_branch_semantics": "one branch is a worker lane containing a bundle of LoopX todos",
            "monitor_lane_semantics": (
                "continuous_monitor todos remain visible as excluded diagnostics and do not "
                "consume worker width or enter todo bundles"
            ),
            "scope_conflict_semantics": (
                "only required_write_scopes create lane mutexes; shared_artifact:* and "
                "shared_implementation:* capabilities describe immutable shared inputs, while "
                "mutable shared builds remain required write scopes"
            ),
            "resource_lane_semantics": (
                "resource_lane:<key> capabilities consume only explicitly declared empty slots; "
                "continuous monitors consume none, and rejected lanes are backfilled in the same call"
            ),
            "branch_fill_semantics": (
                "max_todos_per_branch is a safety ceiling; adaptive profiles decide "
                "bundle size from marginal value and do not pad lanes to fill requested width"
            ),
            "commit_stage": "verified findings merge through explore node/edge/finding events",
            "next_cycle": "merged findings and blocked frontier feed the next planning call",
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
            else "Use quota should-run, then execute each selected worker branch through "
            "normal LoopX claim, lease, todo execution, explore writeback, refresh-state, "
            "and quota spend-slot."
        ),
    }
