"""Additive, owner-local route reconciliation for a Lark event collector."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from .event_collector import (
    CONFIG_SCHEMA_VERSION,
    MAX_ROUTE_COUNT,
    _project_config_path,
    _relative_project_path,
    load_lark_event_collector_config,
)
from .event_inbox import (
    ROUTE_KEY_PATTERN,
    load_lark_event_inbox_config,
)
from .identity_shapes import LARK_CHAT_ID_PATTERN

ROUTE_RECONCILE_SCHEMA_VERSION = "lark_event_collector_route_reconcile_v0"


def _route_reconcile_candidate(
    config: Mapping[str, Any],
    *,
    route_key: str,
    chat_id: str,
    event_inbox_config: str,
) -> tuple[dict[str, str], Path]:
    if config["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise ValueError("collector route reconcile requires a v1 collector config")
    if not ROUTE_KEY_PATTERN.fullmatch(route_key):
        raise ValueError("route_key must be a lowercase public-safe token")
    if not LARK_CHAT_ID_PATTERN.fullmatch(chat_id):
        raise ValueError("chat_id must be a Lark oc_ chat id")
    root = Path(config["project"])
    inbox_ref, inbox_config_path = _relative_project_path(
        root,
        event_inbox_config,
        "event_inbox_config",
    )
    inbox = load_lark_event_inbox_config(
        project=root,
        config_path=inbox_config_path,
    )
    if config["enabled"] and not inbox["enabled"]:
        raise ValueError("enabled collector requires an enabled event inbox")
    if config["enabled"] and not inbox["thread_complete"]:
        raise ValueError(
            "collector lifecycle currently requires configured_chat_all capture"
        )
    reply = inbox["reply"]
    if reply.get("enabled") is True:
        if str(reply.get("chat_id") or "").strip() != chat_id:
            raise ValueError("collector route chat_id must match inbox reply chat_id")
        reply_profile = str(reply.get("sender_profile") or "").strip()
        if config.get("profile") and reply_profile != config["profile"]:
            raise ValueError(
                "collector route reply profile must match the collector profile"
            )
    if config["turn_start_sync"]["enabled"] and (
        inbox["material_review"].get("enabled") is not True
    ):
        raise ValueError(
            "collector turn_start_sync requires material_review on every route"
        )
    return (
        {
            "route_key": route_key,
            "chat_id": chat_id,
            "event_inbox_config": inbox_ref,
        },
        inbox["inbox_path"],
    )


def _collector_config_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("lark collector config must be readable JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("lark collector config must be a JSON object")
    return payload


def _collector_config_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _atomic_write_collector_config(path: Path, payload: Mapping[str, Any]) -> None:
    mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _prepare_route_reconcile(
    *,
    project: str | Path,
    config_path: str | Path,
    route_key: str,
    chat_id: str,
    event_inbox_config: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], bool]:
    config = load_lark_event_collector_config(
        project=project,
        config_path=config_path,
    )
    raw = _collector_config_payload(Path(config["config_path"]))
    candidate, candidate_inbox_path = _route_reconcile_candidate(
        config,
        route_key=route_key,
        chat_id=chat_id,
        event_inbox_config=event_inbox_config,
    )
    exact_present = False
    for route in config["routes"]:
        if route["route_key"] == route_key:
            if (
                route["chat_id"] != chat_id
                or route["event_inbox_config_ref"] != candidate["event_inbox_config"]
            ):
                raise ValueError("collector route_key is already bound differently")
            exact_present = True
        elif route["chat_id"] == chat_id:
            raise ValueError("collector chat_id is already bound to another route")
        elif route["event_inbox_config_ref"] == candidate["event_inbox_config"]:
            raise ValueError(
                "collector event_inbox_config is already bound to another route"
            )
    if not exact_present:
        if len(config["routes"]) >= MAX_ROUTE_COUNT:
            raise ValueError(f"collector routes cannot exceed {MAX_ROUTE_COUNT}")
        existing_inbox_paths = {
            route["inbox"]["inbox_path"]
            for route in config["routes"]
            if route["inbox"]["inbox_path"] is not None
        }
        if candidate_inbox_path in existing_inbox_paths:
            raise ValueError("collector routes must use independent event inbox paths")
    return config, raw, candidate, exact_present


def _route_reconcile_receipt(
    *,
    status: str,
    route_key: str,
    route_count_before: int,
    route_count_after: int,
    config_digest_before: str,
    config_digest_after: str,
    write_performed: bool,
    readback_verified: bool,
    route_state: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": ROUTE_RECONCILE_SCHEMA_VERSION,
        "status": status,
        "route_key": route_key,
        "route_count_before": route_count_before,
        "route_count_after": route_count_after,
        "config_digest_before": config_digest_before,
        "config_digest_after": config_digest_after,
        "write_performed": write_performed,
        "runtime_reload_required": True,
        "runtime_reload_performed": False,
        "runtime_readback_verified": False,
        "chat_id_returned": False,
        "event_inbox_config_returned": False,
        "local_path_returned": False,
        "readback": {"verified": readback_verified, "route_state": route_state},
    }


def _restore_collector_config(
    path: Path,
    payload: Mapping[str, Any],
    *,
    expected_digest: str,
) -> None:
    _atomic_write_collector_config(path, payload)
    restored = _collector_config_payload(path)
    if _collector_config_digest(restored) != expected_digest:
        raise ValueError("collector route rollback readback mismatch")


def _reconcile_lark_event_collector_route_once(
    *,
    project: str | Path,
    config_path: str | Path,
    route_key: str,
    chat_id: str,
    event_inbox_config: str,
    execute: bool = False,
) -> dict[str, Any]:
    config, raw, candidate, exact_present = _prepare_route_reconcile(
        project=project,
        config_path=config_path,
        route_key=route_key,
        chat_id=chat_id,
        event_inbox_config=event_inbox_config,
    )
    before_digest = _collector_config_digest(raw)
    if exact_present:
        return _route_reconcile_receipt(
            status="already_applied",
            route_key=route_key,
            route_count_before=len(config["routes"]),
            route_count_after=len(config["routes"]),
            config_digest_before=before_digest,
            config_digest_after=before_digest,
            write_performed=False,
            readback_verified=True,
            route_state="present",
        )
    proposed = {**raw, "routes": [*raw["routes"], candidate]}
    after_digest = _collector_config_digest(proposed)
    if not execute:
        return _route_reconcile_receipt(
            status="preview_ready",
            route_key=route_key,
            route_count_before=len(config["routes"]),
            route_count_after=len(config["routes"]) + 1,
            config_digest_before=before_digest,
            config_digest_after=after_digest,
            write_performed=False,
            readback_verified=False,
            route_state="planned",
        )
    path = Path(config["config_path"])
    _atomic_write_collector_config(path, proposed)
    try:
        readback = load_lark_event_collector_config(
            project=project,
            config_path=path,
        )
        matches = [
            route
            for route in readback["routes"]
            if route["route_key"] == route_key
            and route["chat_id"] == chat_id
            and route["event_inbox_config_ref"] == candidate["event_inbox_config"]
        ]
        readback_raw = _collector_config_payload(path)
        if len(matches) != 1 or _collector_config_digest(readback_raw) != after_digest:
            raise ValueError("collector route readback mismatch")
    except (OSError, TypeError, ValueError) as readback_error:
        try:
            _restore_collector_config(
                path,
                raw,
                expected_digest=before_digest,
            )
        except (OSError, TypeError, ValueError) as rollback_error:
            raise ValueError(
                "collector route apply failed readback and rollback was not verified"
            ) from rollback_error
        raise ValueError(
            "collector route apply failed readback and was rolled back"
        ) from readback_error
    return _route_reconcile_receipt(
        status="applied",
        route_key=route_key,
        route_count_before=len(config["routes"]),
        route_count_after=len(readback["routes"]),
        config_digest_before=before_digest,
        config_digest_after=after_digest,
        write_performed=True,
        readback_verified=True,
        route_state="present",
    )


def reconcile_lark_event_collector_route(
    *,
    project: str | Path,
    config_path: str | Path,
    route_key: str,
    chat_id: str,
    event_inbox_config: str,
    execute: bool = False,
) -> dict[str, Any]:
    """Plan or atomically apply one redacted, idempotent collector route."""

    if not execute:
        return _reconcile_lark_event_collector_route_once(
            project=project,
            config_path=config_path,
            route_key=route_key,
            chat_id=chat_id,
            event_inbox_config=event_inbox_config,
            execute=False,
        )
    path = _project_config_path(project, config_path)
    with exclusive_file_lock(path, operation="lark_collector_route_reconcile"):
        return _reconcile_lark_event_collector_route_once(
            project=project,
            config_path=path,
            route_key=route_key,
            chat_id=chat_id,
            event_inbox_config=event_inbox_config,
            execute=True,
        )
