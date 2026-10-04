"""Compatibility imports for the moved configuration-backup HTTP owner."""

from __future__ import annotations

import warnings

warnings.warn(
    "loopx.chat_configuration_backup_api moved to loopx.presentation.configuration_backup_api",
    DeprecationWarning,
    stacklevel=2,
)

from ..presentation.configuration_backup_api import (  # noqa: E402
    CONFIGURATION_BACKUP_PATH,
    ConfigurationBackupRequestMixin,
)

__all__ = ["CONFIGURATION_BACKUP_PATH", "ConfigurationBackupRequestMixin"]
