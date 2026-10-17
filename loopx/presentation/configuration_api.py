from __future__ import annotations

from collections.abc import Callable

from . import codex_attached_api as attached_api
from . import workspace_preferences_api as preferences_api
from . import configuration_backup_api as backup_api
from . import goal_transfer_api as transfer_api
from . import state_recovery_api as recovery_api
from . import goal_ownership_api as ownership_api
from . import goal_amendment_api as amendment_api
from . import request_control_api as request_api
from . import goal_storage_api as storage_api
from .. import chat_usage_statistics_api as usage_api
from .. import chat_goal_configuration_api as goal_api
from . import goal_direction_api as direction_api
from .. import chat_machine_configuration_api as machine_api
from .. import chat_operator_provider_api as operator_api
from .. import chat_automation_cadence_api as cadence_api


class ChatConfigurationRequestMixin(
    attached_api.CodexAttachedRequestMixin,
    request_api.RequestControlMixin,
    preferences_api.WorkspacePreferencesRequestMixin,
    amendment_api.GoalAmendmentRequestMixin,
    storage_api.GoalStorageRequestMixin,
    ownership_api.GoalOwnershipRequestMixin,
    usage_api.UsageStatisticsRequestMixin,
    cadence_api.AutomationCadenceRequestMixin,
    goal_api.GoalConfigurationRequestMixin,
    direction_api.GoalDirectionRequestMixin,
    machine_api.MachineConfigurationRequestMixin,
    operator_api.OperatorProviderRequestMixin,
    backup_api.ConfigurationBackupRequestMixin,
    transfer_api.GoalTransferRequestMixin,
    recovery_api.StateRecoveryRequestMixin,
):
    """Expose machine and Goal configuration through one route registry."""

    def _configuration_get_routes(self) -> dict[str, Callable[[], None]]:
        return {
            attached_api.CODEX_ATTACHED_PATH: self._codex_attached_read,
            attached_api.CODEX_ATTACHED_LIVE_PATH: self._codex_attached_live,
            request_api.REQUEST_CONTROL_PATH: self._request_control_read,
            preferences_api.SIDEBAR_GOAL_ORDER_PATH: self._sidebar_order_read,
            amendment_api.CHAT_GOAL_AMENDMENT_PATH: self._amendment_inspect,
            storage_api.CHAT_GOAL_STORAGE_PATH: self._storage_inspect,
            recovery_api.STATE_RECOVERY_PATH: self._state_recovery_list,
            f"{recovery_api.STATE_RECOVERY_PATH}/profile": self._state_recovery_profile,
            ownership_api.CHAT_GOAL_OWNERSHIP_PATH: self._ownership_inspect,
            cadence_api.CHAT_AUTOMATION_CADENCE_PATH: self._cadence_read,
            goal_api.CHAT_GOAL_CONFIGURATION_PATH: self._goal_configuration_inspect,
            direction_api.GOAL_DIRECTION_PATH: self._direction_inspect,
            machine_api.CHAT_MACHINE_CONFIGURATION_PATH: self._machine_configuration_inspect,
            operator_api.CHAT_OPERATOR_PROVIDER_PATH: self._operator_provider_status,
            usage_api.CHAT_USAGE_STATISTICS_PATH: self._usage_statistics_status,
        }

    def _configuration_post_routes(self) -> dict[str, Callable[[], None]]:
        return {
            transfer_api.GOAL_TRANSFER_PATH: self._goal_transfer,
            attached_api.CODEX_ATTACHED_PATH: self._codex_attached_send,
            attached_api.CODEX_ATTACHED_OPEN_PATH: self._codex_attached_open,
            request_api.REQUEST_CONTROL_PATH: self._request_control_write,
            preferences_api.SIDEBAR_GOAL_ORDER_PATH: self._sidebar_order_save,
            amendment_api.CHAT_GOAL_AMENDMENT_PATH: self._amendment_configure,
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/preview": lambda: self._storage_update(action="plan-migration"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/apply": lambda: self._storage_update(action="migrate"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/recover": lambda: self._storage_update(action="migration-readback"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/discard": self._storage_discard,
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/import/preview": lambda: self._storage_import(action="prepare"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/import/apply": lambda: self._storage_import(action="apply"),
            f"{storage_api.CHAT_GOAL_STORAGE_PATH}/import/recover": lambda: self._storage_import(action="readback"),
            f"{backup_api.CONFIGURATION_BACKUP_PATH}/export": self._configuration_backup_export,
            f"{backup_api.CONFIGURATION_BACKUP_PATH}/restore": self._configuration_backup_restore,
            f"{recovery_api.STATE_RECOVERY_PATH}/archive": lambda: self._state_recovery_upload("archive"),
            f"{recovery_api.STATE_RECOVERY_PATH}/manifest": lambda: self._state_recovery_upload("manifest"),
            f"{recovery_api.STATE_RECOVERY_PATH}/verify": self._state_recovery_verify,
            f"{recovery_api.STATE_RECOVERY_PATH}/read": self._state_recovery_read,
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
            direction_api.GOAL_DIRECTION_PATH: self._direction_configure,
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
