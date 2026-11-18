"""Deterministic observation and review contracts for pull-request queues."""

from importlib import import_module
from typing import Any

# Configuration and catalog readers share this namespace with the optional
# review implementation. Resolve existing public exports only when requested.
_EXPORTS = {
    "build_agent_response_contract": "review_contract",
    "build_merge_readiness": "merge_readiness",
    "PULL_REQUEST_REVIEW_MACHINE_DEFAULTS_SCHEMA": "machine_defaults",
    "normalize_pull_request_review_machine_defaults": "machine_defaults",
    "pull_request_review_machine_configuration_namespace": "machine_defaults",
    "review_priority_machine_default": "machine_defaults",
    "build_pull_request_review_queue_observation": "core",
    "build_review_execution_contract": "review_contract",
    "build_review_plan": "review_contract",
    "build_review_template": "review_contract",
    "build_scheduling_policy": "scheduling",
    "DEFAULT_REVIEW_PRIORITY": "scheduling",
    "classify_scheduling_lane": "scheduling",
    "community_feedback_ready": "scheduling",
    "normalize_review_priority": "scheduling",
    "exact_head_key": "selection_execution",
    "materialize_review_execution": "selection_execution",
    "normalize_fresh_audit_exact_heads": "selection_execution",
    "PullRequestSchedulingLane": "scheduling",
    "PullRequestReviewPriority": "scheduling",
    "scheduling_sort_key": "scheduling",
    "scheduling_tier": "scheduling",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = list(_EXPORTS)
