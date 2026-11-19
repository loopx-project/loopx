"""Public issue-fix capability boundaries."""
from __future__ import annotations

from importlib import import_module
from typing import Any

# PR wait observation imports one owner submodule; it does not select report
# sources, PR descriptions or reward-memory workflows. Keep compatibility
# exports identical to their owners without importing them eagerly.
_EXPORTS = {
    "build_issue_fix_pr_description": "pr_description",
    "build_issue_fix_periodic_report_source": "periodic_report",
    "issue_fix_periodic_report_source_adapter": "periodic_report",
    "run_issue_fix_patch_planning_reward_memory": "reward_memory",
    "run_issue_fix_reviewer_artifact_reward_memory": "reward_memory",
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

__all__ = [
    "build_issue_fix_periodic_report_source",
    "build_issue_fix_pr_description",
    "issue_fix_periodic_report_source_adapter",
    "run_issue_fix_patch_planning_reward_memory",
    "run_issue_fix_reviewer_artifact_reward_memory",
]
