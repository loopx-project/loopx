"""Read retained task-lease JSON and report transport errors.

Current lifecycle decisions, generation validation and writes belong to the
TypeScript owner. Historical readers do not mint or rewrite lease records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TASK_LEASE_SCHEMA_VERSION = "task_lease_v0"


class TaskLeaseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


def read_lease(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise TaskLeaseError(
            f"lease file is not valid JSON: {path}",
            code="corrupt_lease",
            payload={"lease_path": str(path), "error": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise TaskLeaseError(
            f"lease file must contain an object: {path}",
            code="corrupt_lease",
            payload={"lease_path": str(path)},
        )
    return payload
