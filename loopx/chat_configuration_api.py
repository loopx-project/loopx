from __future__ import annotations

from collections.abc import Callable

from . import chat_usage_statistics_api as usage_api
from . import chat_goal_configuration_api as goal_api
from . import chat_machine_configuration_api as machine_api
from . import chat_operator_provider_api as operator_api
from . import chat_automation_cadence_api as cadence_api


class ChatConfigurationRequestMixin(
    usage_api.UsageStatisticsRequestMixin,
    cadence_api.AutomationCadenceRequestMixin,
    goal_api.GoalConfigurationRequestMixin,
    machine_api.MachineConfigurationRequestMixin,
    operator_api.OperatorProviderRequestMixin,
):
    """Expose machine and Goal configuration through one route registry."""

    def _configuration_get_routes(self) -> dict[str, Callable[[], None]]:
        return {
            cadence_api.CHAT_AUTOMATION_CADENCE_PATH: self._cadence_read,
            goal_api.CHAT_GOAL_CONFIGURATION_PATH: self._goal_configuration_inspect,
            machine_api.CHAT_MACHINE_CONFIGURATION_PATH: self._machine_configuration_inspect,
            operator_api.CHAT_OPERATOR_PROVIDER_PATH: self._operator_provider_status,
            usage_api.CHAT_USAGE_STATISTICS_PATH: self._usage_statistics_status,
        }

    def _configuration_post_routes(self) -> dict[str, Callable[[], None]]:
        return {
            cadence_api.CHAT_AUTOMATION_CADENCE_PREVIEW_PATH: lambda: self._cadence_update(execute=False),
            cadence_api.CHAT_AUTOMATION_CADENCE_APPLY_PATH: lambda: self._cadence_update(execute=True),
            goal_api.CHAT_GOAL_CONFIGURATION_PREVIEW_PATH: lambda: self._goal_configuration_update(
                execute=False
            ),
            goal_api.CHAT_GOAL_CONFIGURATION_APPLY_PATH: lambda: self._goal_configuration_update(
                execute=True
            ),
            machine_api.CHAT_MACHINE_CONFIGURATION_PREVIEW_PATH: lambda: self._machine_configuration_update(
                execute=False
            ),
            machine_api.CHAT_MACHINE_CONFIGURATION_APPLY_PATH: lambda: self._machine_configuration_update(
                execute=True
            ),
            machine_api.CHAT_MACHINE_CONFIGURATION_ROLLBACK_PATH: self._machine_configuration_rollback,
            operator_api.CHAT_OPERATOR_PROVIDER_PATH: self._operator_provider_update,
            usage_api.CHAT_USAGE_STATISTICS_PATH: self._usage_statistics_update,
        }


__all__ = ["ChatConfigurationRequestMixin"]
