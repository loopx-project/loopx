from __future__ import annotations

from collections.abc import Callable

from . import configuration_backup_api as backup_api
from . import goal_ownership_api as ownership_api
from . import goal_storage_api as storage_api
from .. import chat_usage_statistics_api as usage_api
from .. import chat_goal_configuration_api as goal_api
from .. import chat_machine_configuration_api as machine_api
from .. import chat_operator_provider_api as operator_api
from .. import chat_automation_cadence_api as cadence_api


class ChatConfigurationRequestMixin(
    storage_api.GoalStorageRequestMixin,
    ownership_api.GoalOwnershipRequestMixin,
    usage_api.UsageStatisticsRequestMixin,
    cadence_api.AutomationCadenceRequestMixin,
    goal_api.GoalConfigurationRequestMixin,
    machine_api.MachineConfigurationRequestMixin,
    operator_api.OperatorProviderRequestMixin,
    backup_api.ConfigurationBackupRequestMixin,
):
    """Expose machine and Goal configuration through one route registry."""

    def _configuration_get_routes(self) -> dict[str, Callable[[], None]]:
        return {
            storage_api.CHAT_GOAL_STORAGE_PATH: self._storage_inspect,
            ownership_api.CHAT_GOAL_OWNERSHIP_PATH: self._ownership_inspect,
            cadence_api.CHAT_AUTOMATION_CADENCE_PATH: self._cadence_read,
            goal_api.CHAT_GOAL_CONFIGURATION_PATH: self._goal_configuration_inspect,
            machine_api.CHAT_MACHINE_CONFIGURATION_PATH: self._machine_configuration_inspect,
            operator_api.CHAT_OPERATOR_PROVIDER_PATH: self._operator_provider_status,
            usage_api.CHAT_USAGE_STATISTICS_PATH: self._usage_statistics_status,
        }

    def _configuration_post_routes(self) -> dict[str, Callable[[], None]]:
        return {
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/preview": lambda: self._storage_update(action="plan-migration"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/apply": lambda: self._storage_update(action="migrate"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/recover": lambda: self._storage_update(action="migration-readback"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/import/preview": lambda: self._storage_import(action="prepare"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/import/apply": lambda: self._storage_import(action="apply"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/import/recover": lambda: self._storage_import(action="readback"),
            f"{backup_api.CONFIGURATION_BACKUP_PATH}/export": self._configuration_backup_export,
            f"{backup_api.CONFIGURATION_BACKUP_PATH}/restore": self._configuration_backup_restore,
            f"{ownership_api.CHAT_GOAL_OWNERSHIP_PATH}/preview": lambda: self._ownership_update(execute=False),
            f"{ownership_api.CHAT_GOAL_OWNERSHIP_PATH}/apply": lambda: self._ownership_update(execute=True),
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
