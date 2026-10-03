"""Native filesystem addresses without Goal discovery or routing dependencies."""

from __future__ import annotations

import os
from pathlib import Path


def windows_extended_path(path: Path) -> Path:
    """Address the same Windows file beyond MAX_PATH; leave other hosts alone."""
    if os.name != "nt":
        return path
    address = os.path.abspath(path)
    if address.startswith("\\\\?\\"):
        return Path(address)
    if address.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + address[2:])
    return Path("\\\\?\\" + address)
