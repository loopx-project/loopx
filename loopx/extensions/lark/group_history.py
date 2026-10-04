"""Bounded Lark group-history catch-up for the local event inbox.

The provider keeps chat ids, profiles, pagination tokens, and raw message bodies
owner-local.  Callers receive typed operation/readback state plus an explicitly
local-private projection of links that can be used as evidence by an Agent.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .event_collector import _executable_prefix, load_lark_event_collector_config
from .event_collector_runtime import (
    _is_profile_self_message,
    _profile_app_id,
    _sender_identity,
)
from .event_inbox import EVENT_SCHEMA_VERSION, MESSAGE_ID_PATTERN
from .group_history_cursor import (
    group_history_cursor_digest,
    group_history_cursor_path,
    group_history_source_fingerprint,
    load_group_history_cursor,
    normalize_group_history_timestamp,
    resolve_group_history_window,
    write_group_history_cursor,
)
from .routed_inbox import ingest_routed_lark_event_inbox

EVIDENCE_SCHEMA_VERSION = "lark_group_message_link_evidence_v0"
READBACK_SCHEMA_VERSION = "lark_group_history_readback_v0"
CATCH_UP_SCHEMA_VERSION = "lark_group_history_catch_up_v0"

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
URL_TRAILING_PUNCTUATION = ".,;:!?，。；：！？、)]}）】》」』"
MAX_URL_LENGTH = 2048
MAX_PAGE_SIZE = 50

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _route_context(
    *, project: str | Path, config_path: str | Path, route_key: str
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    config = load_lark_event_collector_config(
        project=project,
        config_path=config_path,
    )
    if not config["enabled"]:
        raise ValueError("Lark group-history catch-up requires an enabled collector")
    matches = [route for route in config["routes"] if route["route_key"] == route_key]
    if len(matches) != 1:
        raise ValueError("route_key must resolve to exactly one collector route")
    route = matches[0]
    if route["inbox"]["thread_complete"] is not True:
        raise ValueError(
            "Lark group-history catch-up requires configured_chat_all capture"
        )
    project_root = Path(config["project"])
    inbox_path = Path(route["inbox"]["inbox_path"])
    inbox_path_ref = inbox_path.relative_to(project_root).as_posix()
    fingerprint = group_history_source_fingerprint(
        route_key=route_key,
        profile=str(config["profile"]),
        chat_id=str(route["chat_id"]),
        event_inbox_config_ref=str(route["event_inbox_config_ref"]),
        inbox_path_ref=inbox_path_ref,
        capture_scope=str(route["inbox"]["capture_scope"]),
    )
    return (
        config,
        route,
        group_history_cursor_path(project_root, route_key),
        fingerprint,
    )


def _find_nested(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        if key in value:
            return value[key]
        for child in value.values():
            if found := _find_nested(child, key):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _find_nested(child, key):
                return found
    return None


def _failed_receipt(
    *,
    route_key: str,
    status: str,
    external_read_performed: bool,
    provider_exit_code: int | None = None,
    invalid_message_count: int | None = None,
    cursor_state_mutated: bool = False,
    inbox_state_mutated: bool = False,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "ok": False,
        "schema_version": CATCH_UP_SCHEMA_VERSION,
        "status": status,
        "route_key": route_key,
        "external_read_performed": external_read_performed,
        "cursor_state_mutated": cursor_state_mutated,
        "inbox_state_mutated": inbox_state_mutated,
        "chat_id_returned": False,
        "profile_returned": False,
        "private_cursor_values_returned": False,
        "provider_payload_returned": False,
        "private_provider_error_returned": False,
        "readback": {
            "schema_version": READBACK_SCHEMA_VERSION,
            "verified": False,
            "cursor_state": "not_committed",
            "cursor_digest": None,
            "inbox_event_count_verified": 0,
            "private_cursor_values_returned": False,
        },
    }
    if provider_exit_code is not None:
        receipt["provider_exit_code"] = provider_exit_code
    if invalid_message_count is not None:
        receipt["invalid_message_count"] = invalid_message_count
    return receipt


def _provider_failure(
    result: subprocess.CompletedProcess[str], *, route_key: str
) -> dict[str, Any]:
    payloads: list[object] = []
    for raw in (result.stderr, result.stdout):
        try:
            payloads.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    codes = {str(_find_nested(payload, "code") or "") for payload in payloads}
    error_types = {str(_find_nested(payload, "type") or "") for payload in payloads}
    if "230027" in codes:
        status = "group_history_permission_required"
    elif "auth" in error_types or "authentication" in error_types:
        status = "group_history_authorization_required"
    else:
        status = "group_history_provider_failed"
    return _failed_receipt(
        route_key=route_key,
        status=status,
        external_read_performed=True,
        provider_exit_code=result.returncode,
    )


def _provider_page(payload: object) -> tuple[list[Mapping[str, Any]], bool, str | None]:
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise ValueError("Lark group-history provider success envelope is invalid")
    if payload.get("identity") not in {None, "bot"}:
        raise ValueError("Lark group-history provider identity must be bot")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise TypeError("Lark group-history provider data is invalid")
    messages = data.get("messages")
    has_more = data.get("has_more")
    page_token = data.get("page_token")
    if not isinstance(messages, list) or any(
        not isinstance(message, Mapping) for message in messages
    ):
        raise ValueError("Lark group-history provider messages are invalid")
    if not isinstance(has_more, bool):
        raise TypeError("Lark group-history provider has_more is invalid")
    if has_more and (not isinstance(page_token, str) or not page_token.strip()):
        raise ValueError("Lark group-history provider page_token is required")
    if not has_more:
        page_token = None
    return list(messages), has_more, str(page_token) if page_token else None


def _canonical_events(
    messages: Sequence[Mapping[str, Any]],
    *,
    chat_id: str,
    profile_app_id: str | None = None,
) -> tuple[list[dict[str, Any]], int, int, int]:
    events: list[dict[str, Any]] = []
    skipped_count = 0
    invalid_count = 0
    self_message_skipped_count = 0
    for message in messages:
        if _is_profile_self_message(message, profile_app_id=profile_app_id):
            skipped_count += 1
            self_message_skipped_count += 1
            continue
        if message.get("deleted") is True:
            skipped_count += 1
            continue
        message_id = str(message.get("message_id") or "").strip()
        content = message.get("content")
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            invalid_count += 1
            continue
        if not isinstance(content, str) or not content.strip():
            skipped_count += 1
            continue
        event: dict[str, Any] = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": message_id,
            "message_id": message_id,
            "create_time": str(message.get("create_time") or "")[:40],
            "content": content,
            "chat_id": chat_id,
        }
        sender_type, sender_id = _sender_identity(message)
        if sender_type:
            event["sender_type"] = sender_type
        if sender_id:
            event["sender_id"] = sender_id
        for field in ("parent_id", "root_id"):
            if message.get(field) not in (None, ""):
                event[field] = message[field]
        # Preserve structured positive *and negative* mention evidence.  The
        # inbox canonicalizer turns this into a typed addressed-to-Bot flag;
        # dropping an empty list/false value would reopen text heuristics.
        for field in ("mentions", "mentioned"):
            if field in message:
                event[field] = message[field]
        events.append(event)
    return events, skipped_count, invalid_count, self_message_skipped_count


def _normalized_url(raw: str) -> str | None:
    candidate = raw.rstrip(URL_TRAILING_PUNCTUATION)
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        return None
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        return None
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def project_lark_group_message_link_evidence(
    events: Sequence[Mapping[str, Any]], *, route_key: str
) -> dict[str, Any]:
    """Return links with message lineage, but never the surrounding body/sender."""

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        message_id = str(event.get("message_id") or "")
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            continue
        for match in URL_PATTERN.finditer(str(event.get("content") or "")):
            url = _normalized_url(match.group(0))
            if url is None or (message_id, url) in seen:
                continue
            seen.add((message_id, url))
            evidence_ref = hashlib.sha256(f"{message_id}\0{url}".encode()).hexdigest()[
                :24
            ]
            items.append(
                {
                    "evidence_ref": f"sha256:{evidence_ref}",
                    "source_kind": "lark_group_message",
                    "evidence_kind": "link",
                    "route_key": route_key,
                    "message_id": message_id,
                    "create_time": str(event.get("create_time") or ""),
                    "url": url,
                }
            )
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "route_key": route_key,
        "item_count": len(items),
        "items": items,
        "owner_private_evidence_returned": bool(items),
        "raw_message_content_returned": False,
        "sender_identity_returned": False,
        "provider_payload_returned": False,
    }


def _provider_argv(
    *,
    command_prefix: Sequence[str],
    profile: str,
    chat_id: str,
    state: Mapping[str, Any],
    page_size: int,
) -> list[str]:
    argv = [
        *command_prefix,
        "--profile",
        profile,
        "im",
        "+chat-messages-list",
        "--chat-id",
        chat_id,
        "--as",
        "bot",
        "--start",
        str(state["window_start"]),
        "--end",
        str(state["window_end"]),
        "--order",
        "asc",
        "--page-size",
        str(page_size),
        "--no-reactions",
        "--format",
        "json",
    ]
    if state.get("next_page_token"):
        argv.extend(["--page-token", str(state["next_page_token"])])
    return argv


def _page_digest(events: Sequence[Mapping[str, Any]], page_token: str | None) -> str:
    values = [str(event.get("message_id") or "") for event in events]
    values.append(page_token or "complete")
    return hashlib.sha256("\0".join(values).encode()).hexdigest()[:16]


def _verify_inbox_events(
    route: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> int:
    inbox = route["inbox"]["inbox_path"]
    verified = 0
    for event in events:
        path = inbox / f"{event['message_id']}.json"
        expected_content = " ".join(str(event.get("content") or "").split())[:1200]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Lark group-history inbox readback failed") from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("message_id") != event["message_id"]
            or payload.get("route_key") != route["route_key"]
            or payload.get("content") != expected_content
        ):
            raise ValueError("Lark group-history inbox readback mismatch")
        verified += 1
    return verified


def _replayed_receipt(*, route_key: str, state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": CATCH_UP_SCHEMA_VERSION,
        "status": "history_complete",
        "route_key": route_key,
        "cursor_transition": "replayed",
        "history_complete": True,
        "requested_count": 0,
        "accepted_count": 0,
        "duplicate_count": 0,
        "skipped_count": 0,
        "link_evidence": project_lark_group_message_link_evidence(
            [], route_key=route_key
        ),
        "external_read_performed": False,
        "cursor_state_mutated": False,
        "inbox_state_mutated": False,
        "chat_id_returned": False,
        "profile_returned": False,
        "private_cursor_values_returned": False,
        "readback": {
            "schema_version": READBACK_SCHEMA_VERSION,
            "verified": True,
            "cursor_state": "replayed",
            "cursor_digest": group_history_cursor_digest(state),
            "inbox_event_count_verified": 0,
            "private_cursor_values_returned": False,
        },
    }


def catch_up_lark_group_history(
    *,
    project: str | Path,
    config_path: str | Path,
    route_key: str,
    start: str,
    page_size: int = MAX_PAGE_SIZE,
    execute: bool = False,
    lark_cli_executable: str = "lark-cli",
    node_executable: str | None = None,
    runner: CommandRunner = subprocess.run,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read one bounded page, ingest it, then advance the private cursor."""

    if not 1 <= int(page_size) <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    requested_start = normalize_group_history_timestamp(start, field="start")
    snapshot_end = (
        (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    config, route, cursor_path, fingerprint = _route_context(
        project=project,
        config_path=config_path,
        route_key=route_key,
    )
    existing = load_group_history_cursor(
        cursor_path,
        route_key=route_key,
        source_fingerprint=fingerprint,
    )
    state, transition, replayed = resolve_group_history_window(
        existing=existing,
        route_key=route_key,
        source_fingerprint=fingerprint,
        requested_start=requested_start,
        snapshot_end=snapshot_end,
    )
    if replayed:
        return _replayed_receipt(route_key=route_key, state=state)

    command_prefix = (
        [node_executable, lark_cli_executable]
        if node_executable
        else _executable_prefix(lark_cli_executable)
    )
    argv = _provider_argv(
        command_prefix=command_prefix,
        profile=str(config["profile"]),
        chat_id=str(route["chat_id"]),
        state=state,
        page_size=int(page_size),
    )
    try:
        result = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return _failed_receipt(
            route_key=route_key,
            status="group_history_provider_unavailable",
            external_read_performed=False,
        )
    if result.returncode != 0:
        return _provider_failure(result, route_key=route_key)
    try:
        payload = json.loads(result.stdout)
        messages, has_more, next_page_token = _provider_page(payload)
        profile_app_id = (
            _profile_app_id(
                runner=runner,
                command_prefix=command_prefix,
                profile=str(config["profile"]),
            )
            if any(_sender_identity(message)[0] == "app" for message in messages)
            else None
        )
        (
            events,
            skipped_count,
            invalid_count,
            self_message_skipped_count,
        ) = _canonical_events(
            messages,
            chat_id=str(route["chat_id"]),
            profile_app_id=profile_app_id,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return _failed_receipt(
            route_key=route_key,
            status="group_history_provider_payload_invalid",
            external_read_performed=True,
        )
    if invalid_count:
        return _failed_receipt(
            route_key=route_key,
            status="group_history_provider_payload_invalid",
            external_read_performed=True,
            invalid_message_count=invalid_count,
        )

    evidence = project_lark_group_message_link_evidence(
        events,
        route_key=route_key,
    )
    ingest = ingest_routed_lark_event_inbox(
        project=config["project"],
        config_path=config["config_path"],
        events=events,
        execute=execute,
    )
    if not execute:
        return {
            "ok": True,
            "schema_version": CATCH_UP_SCHEMA_VERSION,
            "status": "preview_ready",
            "route_key": route_key,
            "cursor_transition": transition,
            "requested_count": len(messages),
            "accepted_count": int(ingest["accepted_count"]),
            "duplicate_count": int(ingest["duplicate_count"]),
            "skipped_count": skipped_count,
            "self_message_skipped_count": self_message_skipped_count,
            "history_complete": not has_more,
            "link_evidence": evidence,
            "external_read_performed": True,
            "cursor_state_mutated": False,
            "inbox_state_mutated": False,
            "chat_id_returned": False,
            "profile_returned": False,
            "private_cursor_values_returned": False,
            "readback": {
                "schema_version": READBACK_SCHEMA_VERSION,
                "verified": False,
                "cursor_state": "not_committed",
                "cursor_digest": None,
                "inbox_event_count_verified": 0,
                "private_cursor_values_returned": False,
            },
        }

    try:
        verified_count = _verify_inbox_events(route, events)
    except ValueError:
        return _failed_receipt(
            route_key=route_key,
            status="group_history_inbox_readback_failed",
            external_read_performed=True,
            inbox_state_mutated=bool(ingest["write_performed"]),
        )
    updated = {
        **state,
        "next_page_token": next_page_token,
        "history_complete": not has_more,
        "page_count": int(state["page_count"]) + 1,
        "message_count": int(state["message_count"]) + len(events),
        "last_page_digest": _page_digest(events, next_page_token),
    }
    if not has_more:
        if updated["window_kind"] == "earlier":
            updated["coverage_start"] = updated["window_start"]
        else:
            updated["coverage_end"] = updated["window_end"]
    try:
        write_group_history_cursor(cursor_path, updated)
        readback = load_group_history_cursor(
            cursor_path,
            route_key=route_key,
            source_fingerprint=fingerprint,
        )
        if readback != updated:
            raise ValueError("Lark group-history cursor readback verification failed")
    except (OSError, TypeError, ValueError):
        return _failed_receipt(
            route_key=route_key,
            status="group_history_cursor_commit_failed",
            external_read_performed=True,
            cursor_state_mutated=cursor_path.is_file(),
            inbox_state_mutated=bool(ingest["write_performed"]),
        )
    return {
        "ok": True,
        "schema_version": CATCH_UP_SCHEMA_VERSION,
        "status": "history_complete" if not has_more else "page_captured",
        "route_key": route_key,
        "cursor_transition": transition,
        "requested_count": len(messages),
        "accepted_count": int(ingest["accepted_count"]),
        "duplicate_count": int(ingest["duplicate_count"]),
        "skipped_count": skipped_count,
        "self_message_skipped_count": self_message_skipped_count,
        "history_complete": not has_more,
        "link_evidence": evidence,
        "external_read_performed": True,
        "cursor_state_mutated": True,
        "inbox_state_mutated": bool(ingest["write_performed"]),
        "chat_id_returned": False,
        "profile_returned": False,
        "private_cursor_values_returned": False,
        "readback": {
            "schema_version": READBACK_SCHEMA_VERSION,
            "verified": True,
            "cursor_state": "committed",
            "cursor_digest": group_history_cursor_digest(updated),
            "inbox_event_count_verified": verified_count,
            "private_cursor_values_returned": False,
        },
    }
