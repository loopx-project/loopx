from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any

from .contract import MachineConfigurationNamespace, MachineConfigurationRegistry

# Namespace id, owner module and factory. Owners stay at the composition
# boundary: the generic contract does not depend on any consumer capability,
# and a reader loads only the namespaces it resolves.
_BUILTIN_NAMESPACES = (
    (
        "context_delivery",
        "loopx.capabilities.machine_configuration.context_delivery",
        "context_delivery_machine_configuration_namespace",
    ),
    (
        "explore_harness",
        "loopx.capabilities.explore.machine_defaults",
        "explore_machine_configuration_namespace",
    ),
    (
        "codex_chat",
        "loopx.capabilities.native_chat.machine_defaults",
        "codex_chat_machine_configuration_namespace",
    ),
    (
        "goal_storage",
        "loopx.capabilities.machine_configuration.goal_storage",
        "goal_storage_machine_configuration_namespace",
    ),
    (
        "manager_runtime",
        "loopx.capabilities.manager_runtime.machine_profile",
        "manager_runtime_machine_configuration_namespace",
    ),
    (
        "periodic_report",
        "loopx.capabilities.periodic_report.machine_defaults",
        "periodic_report_machine_configuration_namespace",
    ),
    (
        "todo_replan_cadence",
        "loopx.capabilities.todo_replan_cadence.machine_defaults",
        "todo_replan_cadence_machine_configuration_namespace",
    ),
    (
        "change_quality_qualification",
        "loopx.capabilities.change_quality.machine_defaults",
        "change_quality_machine_configuration_namespace",
    ),
    (
        "pull_request_review",
        "loopx.capabilities.pr_review_queue.machine_defaults",
        "pull_request_review_machine_configuration_namespace",
    ),
    (
        "steward_executor",
        "loopx.capabilities.steward_executor.machine_defaults",
        "steward_executor_machine_configuration_namespace",
    ),
)


def _owner(module: str, factory: str) -> Callable[[], MachineConfigurationNamespace]:
    def load() -> MachineConfigurationNamespace:
        contract: MachineConfigurationNamespace = getattr(
            import_module(module), factory
        )()
        return contract

    return load


def build_builtin_machine_configuration_registry() -> MachineConfigurationRegistry:
    registry = MachineConfigurationRegistry()
    for namespace, module, factory in _BUILTIN_NAMESPACES:
        registry.register_deferred(namespace, _owner(module, factory))
    return registry


def project_goal_with_builtin_machine_configuration(
    goal: Mapping[str, Any],
    machine_configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compose capability-owned live defaults into one read-only Goal projection."""

    from ..change_quality.machine_defaults import (
        apply_change_quality_machine_default,
    )
    from ..todo_replan_cadence.machine_defaults import (
        apply_todo_replan_cadence_machine_default,
    )

    from ..explore.machine_defaults import apply_explore_machine_default

    goal = apply_explore_machine_default(goal, machine_configuration)
    projected = apply_todo_replan_cadence_machine_default(
        goal, machine_configuration
    )
    return dict(
        apply_change_quality_machine_default(projected, machine_configuration)
    )


def builtin_machine_inheritable_goal_overrides(
    goal: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Project explicit Goal values for capabilities with live machine defaults."""

    from ..change_quality.goal_configuration import (
        configuration_summary as change_quality_summary,
    )
    from ..todo_replan_cadence.goal_configuration import (
        configuration_summary as cadence_summary,
    )

    from ..pr_review_queue.goal_configuration import configuration_summary as pr_review_summary

    from ..explore.machine_defaults import configuration_summary as explore_summary

    summaries = {
        "explore_harness": explore_summary(goal),
        "pull_request_review": pr_review_summary(goal),
        "change_quality_qualification": change_quality_summary(goal),
        "todo_replan_cadence": cadence_summary(goal),
    }
    return {
        capability_id: summary
        for capability_id, summary in summaries.items()
        if summary is not None
    }


__all__ = [
    "build_builtin_machine_configuration_registry",
    "builtin_machine_inheritable_goal_overrides",
    "project_goal_with_builtin_machine_configuration",
]
