from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...capabilities.periodic_report.adapters import PeriodicReportAdapterRegistry
from ...capabilities.periodic_report.bindings import (
    DELIVERY_INTENT_SCHEMA,
    GENERATION_BUNDLE_SCHEMA,
    build_periodic_report_generation_bundle,
)
from ...capabilities.periodic_report.core import _reject_raw_keys
from ...capabilities.periodic_report.incremental import (
    commit_periodic_report_publication_cursor,
    find_periodic_report_publication_candidate,
)
from ...capabilities.periodic_report.machine_defaults import (
    build_periodic_report_delivery_authority,
    normalize_periodic_report_delivery_authority,
    resolve_goal_periodic_report_subscription,
)
from ...capabilities.periodic_report.machine_store import (
    read_periodic_report_machine_defaults,
)
from ...history import load_registry
from . import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION
from .goal_channel_contracts import (
    binding_for_goal,
    default_goal_channel_binding_path,
    goal_from_registry,
    read_goal_channel_binding,
)
from .goal_channel_message_delivery import GoalChannelMessageDeliverySession
from .goal_channel_targets import (
    default_goal_channel_target_path,
    goal_channel_target_for_name,
    read_goal_channel_targets,
)
from .presentation.kanban import CommandRunner, default_subprocess_runner
from .presentation.periodic_report import periodic_report_lark_sink_adapter
from ...presentation.public_safety import redact_public_text


GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA = (
    "periodic_report_goal_channel_delivery_request_v0"
)
GOAL_CHANNEL_DELIVERY_RESULT_SCHEMA = "periodic_report_goal_channel_delivery_result_v0"
ANNOUNCEMENT_IDEMPOTENCY_SCHEMA = "periodic_report_goal_channel_announcement_v1"
_ANNOUNCEMENT_KINDS = ("hosted_report", "lark_document")
_ANNOUNCEMENT_FOOTER = "LoopX periodic report · Goal Channel"


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _text(value: object, label: str, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _reject_unknown_fields(
    value: Mapping[str, Any], *, allowed: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _announcements(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(
            "Goal Channel periodic report delivery requires exactly two announcements"
        )
    normalized: list[dict[str, str]] = []
    for index, expected_kind in enumerate(_ANNOUNCEMENT_KINDS):
        label = f"delivery_intent.announcements[{index}]"
        raw = _mapping(value[index], label)
        _reject_unknown_fields(
            raw,
            allowed={"kind", "title", "url"},
            label=label,
        )
        if set(raw) != {"kind", "title", "url"}:
            raise ValueError(f"{label} requires kind, title, and url")
        kind = str(raw.get("kind") or "").strip()
        if kind != expected_kind:
            raise ValueError(
                "Goal Channel announcements must be ordered as hosted_report, "
                "lark_document"
            )
        url = _text(raw.get("url"), f"{label}.url")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{label}.url must be an https URL")
        normalized.append(
            {
                "kind": kind,
                "title": _text(raw.get("title"), f"{label}.title", maximum=72),
                "url": url,
            }
        )
    return normalized


def _next_action_guidance(document: Mapping[str, Any]) -> str | None:
    primary_items: list[dict[str, Any]] = []
    for section in document.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        for item in section.get("items") or []:
            if (
                isinstance(item, Mapping)
                and str(item.get("visibility") or "primary") == "primary"
            ):
                primary_items.append(dict(item))
    candidates = [
        item.get("summary") or item.get("title")
        for item in primary_items
        if item.get("content_kind") == "next_action"
    ]
    if not candidates:
        candidates = [
            item.get("next_action") for item in primary_items if item.get("next_action")
        ]
    if not candidates:
        return None
    guidance = str(redact_public_text(candidates[0], limit=360)).strip()
    return guidance or None


def _announcement_markdown(
    announcement: Mapping[str, str], *, next_action: str | None
) -> str:
    if announcement["kind"] == "hosted_report":
        guidance = f"\n\n下一步：{next_action}" if next_action else ""
        return f"本期阶段周报已发布。{guidance}\n\n[查看周报]({announcement['url']})"
    return f"配套 Lark 文档已同步。\n\n[查看 Lark 文档]({announcement['url']})"


def _announcement_idempotency_key(
    *,
    delivery_idempotency_key: str,
    announcement: Mapping[str, str],
    content: str,
) -> str:
    material = "\0".join(
        (
            ANNOUNCEMENT_IDEMPOTENCY_SCHEMA,
            delivery_idempotency_key,
            announcement["kind"],
            announcement["title"],
            content,
            _ANNOUNCEMENT_FOOTER,
        )
    )
    return (
        "periodic-report-announcement-v1:"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()
    )


def _normalized_generation_bundle(raw: object) -> dict[str, Any]:
    supplied = _mapping(raw, "generation_bundle")
    if supplied.get("schema_version") != GENERATION_BUNDLE_SCHEMA:
        raise ValueError(f"generation_bundle must use {GENERATION_BUNDLE_SCHEMA}")
    normalized = build_periodic_report_generation_bundle(
        document=_mapping(supplied.get("document"), "generation_bundle.document"),
        artifacts=[
            _mapping(item, "generation_bundle.artifacts[]")
            for item in supplied.get("artifacts") or []
        ],
    )
    if supplied != normalized:
        raise ValueError("generation_bundle does not match its normalized receipts")
    return normalized


def _resolved_goal_channel_binding(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    expected_authority: Mapping[str, Any],
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    goal = goal_from_registry(registry, goal_id)
    subscription = resolve_goal_periodic_report_subscription(
        goal,
        read_periodic_report_machine_defaults(runtime_root),
    )
    if subscription.get("enabled") is not True:
        raise ValueError("periodic report delivery subscription is disabled")
    current_authority = build_periodic_report_delivery_authority(subscription)
    if current_authority != dict(expected_authority):
        raise ValueError("periodic report delivery subscription authority drifted")

    payload = read_goal_channel_binding(
        default_goal_channel_binding_path(registry_path)
    )
    raw = binding_for_goal(payload, goal_id)
    authorized_target_ref = str(expected_authority.get("route_ref") or "").strip()
    if not authorized_target_ref:
        raise ValueError("periodic report subscription route is missing")
    target_ref = str((raw or {}).get("target_ref") or "").strip()
    if raw is None:
        target_ref = authorized_target_ref
        raw = {
            "goal_id": goal_id,
            "provider": "lark",
            "enabled": True,
            "target_ref": target_ref,
            "channel": {},
            "identity": {},
        }
    elif target_ref != authorized_target_ref:
        raise ValueError(
            "periodic report Goal Channel binding does not match the authorized route"
        )
    target = None
    if target_ref:
        target = goal_channel_target_for_name(
            read_goal_channel_targets(default_goal_channel_target_path(runtime_root)),
            target_ref,
        )
        if target is None:
            raise ValueError("periodic report Goal Channel target is missing")
    resolution_payload = payload
    if binding_for_goal(payload, goal_id) is None:
        resolution_payload = {
            **payload,
            "bindings": {
                **dict(payload.get("bindings") or {}),
                goal_id: raw,
            },
        }
    resolved = binding_for_goal(
        resolution_payload,
        goal_id,
        provider_target=target,
    )
    if resolved is None:
        raise ValueError("periodic report Goal Channel binding is incomplete")
    return resolved


def _validate_extension_activation(value: Mapping[str, Any]) -> None:
    permissions = value.get("required_permissions")
    if (
        value.get("schema_version") != "loopx_extension_activation_v0"
        or value.get("extension_id") != LARK_EXTENSION_ID
        or value.get("enabled") is not True
        or value.get("doctor_verified") is not True
        or not isinstance(permissions, list)
        or LARK_GOAL_CHANNEL_PERMISSION not in permissions
    ):
        raise ValueError(
            "active Lark extension does not authorize Goal Channel delivery"
        )


def _normalized_delivery_request(
    request: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    str,
    list[dict[str, str]],
    dict[str, Any],
]:
    payload = _mapping(request, "request")
    _reject_unknown_fields(
        payload,
        allowed={
            "schema_version",
            "generation_bundle",
            "delivery_authority",
            "delivery_intent",
        },
        label="request",
    )
    if payload.get("schema_version") != GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA:
        raise ValueError(f"request must use {GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA}")
    generation = _normalized_generation_bundle(payload.get("generation_bundle"))
    authority = normalize_periodic_report_delivery_authority(
        payload.get("delivery_authority")
    )
    intent = _mapping(payload.get("delivery_intent"), "request.delivery_intent")
    identity_override_keys = sorted(
        {
            "bot_app_id",
            "bot_display_name",
            "chat_id",
            "identity_mode",
            "lark_profile",
            "profile",
            "sender_identity",
            "sender_profile",
        }.intersection(intent)
    )
    if identity_override_keys:
        raise ValueError(
            "periodic report sender identity is owned by Goal Channel; "
            "caller overrides are forbidden: " + ", ".join(identity_override_keys)
        )
    _reject_unknown_fields(
        intent,
        allowed={
            "schema_version",
            "kind",
            "sink_id",
            "sink_kind",
            "idempotency_key",
            "artifact_id",
            "announcements",
        },
        label="delivery_intent",
    )
    if intent.get("schema_version") != DELIVERY_INTENT_SCHEMA:
        raise ValueError(f"delivery_intent must use {DELIVERY_INTENT_SCHEMA}")
    if (
        intent.get("kind") != "goal_channel"
        or intent.get("sink_kind") != "lark_message"
    ):
        raise ValueError("delivery_intent must select Goal Channel Lark delivery")
    sink_id = _text(intent.get("sink_id"), "delivery_intent.sink_id", maximum=128)
    idempotency_key = _text(
        intent.get("idempotency_key"),
        "delivery_intent.idempotency_key",
        maximum=256,
    )
    announcements = _announcements(intent.get("announcements"))
    _reject_raw_keys(payload, "request")
    artifacts = [
        artifact
        for artifact in generation["artifacts"]
        if artifact.get("renderer_kind") == "markdown"
        and (
            not intent.get("artifact_id")
            or artifact.get("artifact_id") == str(intent["artifact_id"]).strip()
        )
    ]
    if len(artifacts) != 1:
        raise ValueError("delivery intent must resolve exactly one Markdown artifact")
    return generation, authority, sink_id, idempotency_key, announcements, artifacts[0]


def _delivery_status(*, satisfied: bool, execute: bool) -> str:
    if satisfied:
        return "satisfied"
    if execute:
        return "readback_unverified"
    return "pending_execution"


def deliver_periodic_report_to_goal_channel(
    request: Mapping[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    extension_activation: Mapping[str, Any],
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Deliver one generated report through the Goal-bound project Bot only."""

    _validate_extension_activation(extension_activation)
    generation, authority, sink_id, idempotency_key, announcements, artifact = (
        _normalized_delivery_request(request)
    )
    if authority["goal_id"] != goal_id:
        raise ValueError("periodic report delivery authority Goal identity changed")

    binding = _resolved_goal_channel_binding(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        expected_authority=authority,
    )
    session = GoalChannelMessageDeliverySession(
        goal_id=goal_id,
        binding=binding,
        binding_lock_path=default_goal_channel_binding_path(registry_path),
        target_lock_path=default_goal_channel_target_path(runtime_root),
        history_start_at=str(generation["document"]["generated_at"]),
        resolve_current_binding=lambda: _resolved_goal_channel_binding(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            expected_authority=authority,
        ),
        runner=runner,
    )
    registry = PeriodicReportAdapterRegistry()
    registry.register_sink(
        periodic_report_lark_sink_adapter(
            send=session.send,
            readback=session.readback,
            resolve_goal_channel=session.resolve,
            verify_goal_channel=session.verify,
            sink_id=sink_id,
        )
    )
    next_action = _next_action_guidance(generation["document"])
    message_results: list[dict[str, Any]] = []
    for announcement in announcements:
        content = _announcement_markdown(announcement, next_action=next_action)
        announcement_idempotency_key = _announcement_idempotency_key(
            delivery_idempotency_key=idempotency_key,
            announcement=announcement,
            content=content,
        )
        result = registry.deliver(
            sink_id,
            {
                **artifact,
                "content": content,
                "content_digest": "sha256:"
                + hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            {
                "execute": bool(execute),
                "goal_id": goal_id,
                "idempotency_key": announcement_idempotency_key,
                "title": announcement["title"],
                "footer": _ANNOUNCEMENT_FOOTER,
            },
        )
        message_results.append({"kind": announcement["kind"], **result})
    satisfied = bool(
        execute
        and len(message_results) == 2
        and len({str(result.get("receipt_ref") or "") for result in message_results})
        == 2
        and all(
            result.get("status") == "sent"
            and result.get("readback_verified") is True
            and result.get("goal_channel_verified") is True
            and result.get("sender_identity_verified") is True
            for result in message_results
        )
    )
    sink_status = "sent" if satisfied else "unknown" if execute else "pending"
    sink_result = {
        "status": sink_status,
        "readback_verified": satisfied,
        "goal_channel_verified": satisfied,
        "sender_identity_verified": satisfied,
        "external_writes_performed": any(
            result.get("external_writes_performed") is True
            for result in message_results
        ),
        "message_results": message_results,
    }
    publication_cursor = None
    if satisfied:
        generation_id = str(generation["generation_receipt"]["generation_id"])
        candidate = find_periodic_report_publication_candidate(
            runtime_root=runtime_root,
            goal_id=goal_id,
            generation_id=generation_id,
        )
        if candidate is not None:
            publication_cursor = commit_periodic_report_publication_cursor(
                runtime_root=runtime_root,
                candidate=candidate,
                publication_id=(
                    "goal-channel-"
                    + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
                ),
                delivered_at=datetime.now(timezone.utc).isoformat(),
                covered_until=str(generation["document"]["period_window"]["end_at"]),
            )
    return {
        "ok": bool(satisfied or not execute),
        "schema_version": GOAL_CHANNEL_DELIVERY_RESULT_SCHEMA,
        "status": _delivery_status(satisfied=satisfied, execute=execute),
        "intent_satisfied": satisfied,
        "generation_id": generation["generation_receipt"]["generation_id"],
        "sink_result": sink_result,
        "publication_cursor": publication_cursor,
        "incremental_baseline": (
            candidate.get("incremental_baseline")
            if satisfied and candidate is not None
            else None
        ),
        "boundary": {
            "goal_channel_binding_required": False,
            "goal_channel_binding_preferred": True,
            "machine_default_route_allowed_when_unbound": True,
            "project_bot_identity_required": True,
            "caller_identity_override_allowed": False,
            "exact_sender_and_chat_readback_required": True,
            "exact_history_dedupe_required": True,
            "rendered_announcement_idempotency_bound": True,
            "sender_evidence_source": "message_readback",
            "external_writes_performed": sink_result.get("external_writes_performed")
            is True,
        },
    }


__all__ = [
    "ANNOUNCEMENT_IDEMPOTENCY_SCHEMA",
    "DELIVERY_INTENT_SCHEMA",
    "GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA",
    "GOAL_CHANNEL_DELIVERY_RESULT_SCHEMA",
    "deliver_periodic_report_to_goal_channel",
]
