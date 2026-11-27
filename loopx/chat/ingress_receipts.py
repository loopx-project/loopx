"""Idempotent steering and ingress receipt operations for ``ChatIngressStore``."""
from __future__ import annotations

from contextlib import AbstractContextManager
import json
import os
from pathlib import Path
from typing import cast

from .ingress_common import IngressStoreHostProtocol, LockFileFn, as_str, read_chat_json


def strict_ingress_receipt(
    host: IngressStoreHostProtocol,
    session_id: str,
    ingress_id: str,
) -> dict[str, object] | None:
    """Absent is ``None``; any other unreadable receipt fails closed."""
    from ..chat_store import CHAT_INGRESS_SCHEMA_VERSION
    from ..control_plane.collaboration.source_chat_observation import _opaque_id

    # The receipt is stored under the normalized id, so a replay that repeats
    # the original (padded) text must be matched against that same id.
    ingress_id = _opaque_id(ingress_id, field="client_ingress_id")
    path = host._ingress_path(session_id, ingress_id)
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if os.path.lexists(path):
            # A link whose target is gone is an existing entry, not absence:
            # replacing it would resend a message that may have been delivered.
            raise OSError("Ingress receipt is unreadable") from exc
        return None
    except (OSError, ValueError) as exc:
        raise OSError("Ingress receipt is unreadable") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != CHAT_INGRESS_SCHEMA_VERSION
        or value.get("session_id") != session_id
        or value.get("client_ingress_id") != ingress_id
    ):
        raise OSError("Ingress receipt is unreadable")
    return cast(dict[str, object], value)


def steering_delivery_lock(
    host: IngressStoreHostProtocol,
    session_id: str,
    ingress_id: str,
    *,
    lock_fn: LockFileFn,
) -> AbstractContextManager[Path]:
    """Serialize one native send and its later readback across controllers."""
    return lock_fn(
        host._ingress_path(session_id, ingress_id).with_suffix(".delivery"),
        agent_id="loopx-chat",
        operation="chat_steering_delivery",
    )


def create_ingress_receipt(
    host: IngressStoreHostProtocol,
    session_id: str,
    *,
    client_ingress_id: str,
    mode: str,
    message: str,
    expected_turn_id: str | None = None,
    lock_fn: LockFileFn,
) -> tuple[dict[str, object], bool]:
    """Reserve one idempotent external ingress before provider delivery."""
    from ..chat import require_matching_replay
    from ..chat_store import (
        CHAT_INGRESS_SCHEMA_VERSION,
        _atomic_write_json,
        utc_now,
    )
    from ..control_plane.collaboration.source_chat_observation import _opaque_id

    if host.load_session(session_id) is None:
        raise KeyError("chat session was not found")
    path = host._ingress_path(session_id, client_ingress_id)
    with lock_fn(
        path,
        agent_id="loopx-chat",
        operation="create_chat_ingress_receipt",
    ):
        # A damaged receipt may record a delivered send. It is not absence:
        # recreating it would resend the message and erase that evidence.
        existing = host._strict_ingress_receipt(session_id, client_ingress_id)
        if existing is not None:
            request: dict[str, object] = {
                "mode": _opaque_id(mode, field="mode"),
                "message": str(message),
            }
            if expected_turn_id is not None or "expected_turn_id" in existing:
                request["expected_turn_id"] = expected_turn_id
            require_matching_replay(
                existing,
                identity="client_ingress_id",
                request=request,
            )
            return existing, False
        now = utc_now()
        payload: dict[str, object] = {
            "schema_version": CHAT_INGRESS_SCHEMA_VERSION,
            "client_ingress_id": _opaque_id(
                client_ingress_id,
                field="client_ingress_id",
            ),
            "session_id": session_id,
            "mode": _opaque_id(mode, field="mode"),
            "status": "pending",
            "message": str(message),
            "active_turn_id": None,
            **(
                {"expected_turn_id": _opaque_id(expected_turn_id, field="expected_turn_id")}
                if expected_turn_id is not None
                else {}
            ),
            "error_code": None,
            "created_at": now,
            "updated_at": now,
        }
        _atomic_write_json(path, payload)
        os.chmod(path, 0o600)
        return payload, True


def update_ingress_receipt(
    host: IngressStoreHostProtocol,
    session_id: str,
    client_ingress_id: str,
    lock_fn: LockFileFn,
    /,
    **changes: object,
) -> dict[str, object]:
    """Update allowed fields on an existing Chat ingress receipt under its file lock."""
    from ..chat_store import (
        CHAT_INGRESS_SCHEMA_VERSION,
        _atomic_write_json,
        utc_now,
    )

    path = host._ingress_path(session_id, client_ingress_id)
    with lock_fn(
        path,
        agent_id="loopx-chat",
        operation="update_chat_ingress_receipt",
    ):
        payload = read_chat_json(path)
        if payload.get("schema_version") != CHAT_INGRESS_SCHEMA_VERSION:
            raise KeyError("chat ingress receipt was not found")
        allowed = {"status", "active_turn_id", "error_code", "native_steering"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported chat ingress fields: {sorted(unknown)}")
        payload.update(changes)
        payload["updated_at"] = utc_now()
        _atomic_write_json(path, payload, preserve_mode=True)
        return payload


def loopx_ingress(host: IngressStoreHostProtocol, session_id: str) -> list[dict[str, object]]:
    """Read the existing ingress ledger; no second queue or delivery truth."""
    rows = [
        read_chat_json(path)
        for path in (host._session_dir(session_id) / "ingress").glob("*.json")
    ]
    return sorted(
        (row for row in rows if row.get("mode") in {"loopx_queue", "loopx_inbox"}),
        key=lambda row: (as_str(row.get("created_at")), as_str(row.get("client_ingress_id"))),
    )
