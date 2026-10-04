"""Compatibility imports for the moved configuration-backup owner."""

from __future__ import annotations

import warnings

warnings.warn(
    "loopx.configuration_backup moved to loopx.configuration.backup",
    DeprecationWarning,
    stacklevel=2,
)

from ..configuration.backup import (  # noqa: E402
    capture_configuration_backup,
    restore_configuration_backup,
    verify_configuration_backup,
)

__all__ = [
    "capture_configuration_backup",
    "restore_configuration_backup",
    "verify_configuration_backup",
]
