from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import write_json_atomic


def write_private_json_atomic(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    """Lark private-state JSON; kernel owner performs the atomic rename."""
    write_json_atomic(
        path,
        payload,
        mode=0o600,
        fsync_directory=True,
        indent=2,
    )
