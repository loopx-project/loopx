"""Local task-lease record codec and generation normalization.

This module owns the JSON/tombstone shape at the existing file adapter edge.
It does not decide coordination transitions or perform locking.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..runtime.time import utc_isoformat


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


def write_lease(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{id(payload)}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def lease_epoch(lease: dict[str, Any] | None) -> int:
    """Return the authority-owned generation for one todo's lease lineage."""

    if not lease:
        return 0
    raw_epoch = lease.get("lease_epoch")
    if raw_epoch is None:
        # A pre-lease_epoch record is the first visible generation.
        return 1
    if isinstance(raw_epoch, bool):
        # bool is an int subclass: JSON true must not become epoch 1.
        raise TaskLeaseError(
            "lease epoch must be a positive integer",
            code="corrupt_lease",
            payload={"lease_epoch": raw_epoch},
        )
    try:
        epoch = int(raw_epoch)
    except (TypeError, ValueError) as exc:
        raise TaskLeaseError(
            "lease epoch must be a positive integer",
            code="corrupt_lease",
            payload={"lease_epoch": raw_epoch},
        ) from exc
    if epoch <= 0:
        raise TaskLeaseError(
            "lease epoch must be a positive integer",
            code="corrupt_lease",
            payload={"lease_epoch": raw_epoch},
        )
    return epoch


def lease_version(lease: dict[str, Any] | None) -> int:
    """Normalize the local CAS version without leaking ``ValueError``."""

    raw_version = (lease or {}).get("version")
    if raw_version is None:
        return 0
    if isinstance(raw_version, bool):
        raise TaskLeaseError(
            "lease version must be a non-negative integer",
            code="corrupt_lease",
            payload={"version": raw_version},
        )
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise TaskLeaseError(
            "lease version must be a non-negative integer",
            code="corrupt_lease",
            payload={"version": raw_version},
        ) from exc
    if version < 0:
        raise TaskLeaseError(
            "lease version must be a non-negative integer",
            code="corrupt_lease",
            payload={"version": raw_version},
        )
    return version


def lease_acquire_ttl_seconds(lease: dict[str, Any] | None) -> int | None:
    """Read the acquire fingerprint only for acquire replay decisions."""

    raw_ttl = (lease or {}).get("acquire_ttl_seconds")
    if raw_ttl is None:
        return None
    if isinstance(raw_ttl, bool):
        raise TaskLeaseError(
            "lease acquire_ttl_seconds must be an integer",
            code="corrupt_lease",
            payload={"acquire_ttl_seconds": raw_ttl},
        )
    try:
        return int(raw_ttl)
    except (TypeError, ValueError) as exc:
        raise TaskLeaseError(
            "lease acquire_ttl_seconds must be an integer",
            code="corrupt_lease",
            payload={"acquire_ttl_seconds": raw_ttl},
        ) from exc


def released_lease_payload(
    lease: dict[str, Any],
    *,
    at: datetime,
) -> dict[str, Any]:
    released = dict(lease)
    released["lease_epoch"] = lease_epoch(lease)
    released["status"] = "released"
    released["released_at"] = utc_isoformat(at)
    released["updated_at"] = utc_isoformat(at)
    return released


def assert_expected_version(
    lease: dict[str, Any] | None,
    expected_version: int | None,
) -> None:
    if expected_version is None:
        return
    actual = lease_version(lease)
    if actual != expected_version:
        raise TaskLeaseError(
            f"lease version mismatch: expected {expected_version}, got {actual}",
            code="version_mismatch",
            payload={"expected_version": expected_version, "actual_version": actual},
        )


def require_expected_version(expected_version: int | None, *, action: str) -> int:
    if expected_version is None:
        raise TaskLeaseError(
            f"task lease {action} requires the current lease version",
            code="version_required",
            payload={"action": action},
        )
    if isinstance(expected_version, bool):
        # bool is an int subclass; JSON true from a tool argument must not
        # silently become expected version 1.
        raise TaskLeaseError(
            f"task lease {action} requires an integer lease version",
            code="version_required",
            payload={"action": action, "expected_version": expected_version},
        )
    return int(expected_version)


def build_lease(
    *,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    write_scopes: list[str],
    acquire_ttl_seconds: int,
    version: int,
    lease_epoch: int,
    acquired_at: str,
    updated_at: str,
    expires_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": TASK_LEASE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "owner": owner,
        "idempotency_key": idempotency_key,
        "write_scopes": write_scopes,
        "acquire_ttl_seconds": acquire_ttl_seconds,
        "version": version,
        "lease_epoch": lease_epoch,
        "acquired_at": acquired_at,
        "updated_at": updated_at,
        "expires_at": expires_at,
        "status": "active",
    }
