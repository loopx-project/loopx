from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import MachineConfigurationRegistry


def build_builtin_machine_configuration_registry() -> MachineConfigurationRegistry:
    # Imports stay at the composition boundary: the generic contract does not
    # depend on any consumer capability.
    from ..change_quality.machine_defaults import (
        change_quality_machine_configuration_namespace,
    )
    from ..periodic_report.machine_defaults import (
        periodic_report_machine_configuration_namespace,
    )
    from ..manager_runtime.machine_profile import (
        manager_runtime_machine_configuration_namespace,
    )
    from ..pr_review_queue.machine_defaults import (
        pull_request_review_machine_configuration_namespace,
    )
    from ..steward_executor.machine_defaults import (
        steward_executor_machine_configuration_namespace,
    )
    from ..todo_replan_cadence.machine_defaults import (
        todo_replan_cadence_machine_configuration_namespace,
    )

    from .goal_storage import goal_storage_machine_configuration_namespace

    return (
        MachineConfigurationRegistry()
        .register(goal_storage_machine_configuration_namespace())
        .register(manager_runtime_machine_configuration_namespace())
        .register(periodic_report_machine_configuration_namespace())
        .register(todo_replan_cadence_machine_configuration_namespace())
        .register(change_quality_machine_configuration_namespace())
        .register(pull_request_review_machine_configuration_namespace())
        .register(steward_executor_machine_configuration_namespace())
    )


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

    summaries = {
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
