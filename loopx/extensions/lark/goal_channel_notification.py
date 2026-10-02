"""Public-safe projection of Goal Channel Lark notification binding state.

The projection summarizes each goal's `.loopx/goal-channel.json` binding plus
its human-gate auto-notify marker for status consumers. It is a read model,
not a source of truth, and never copies private provider material such as
chat ids, message ids, base tokens, or sender profiles into its output.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.goals.goal_channel_projection import (
    compact_goal_channel_text,
)
from ...control_plane.runtime.runtime_projection_route import (
    resolve_goal_source_runtime_route,
)
from ...history import load_registry
from ...registry import registry_goals
from .goal_channel_contracts import (
    BLOCKED_NOTICE_RETIRED_STATES,
    BlockedNoticeReceiptState,
    assert_public_packet,
    binding_for_goal,
    blocked_notice_receipt_matches_target,
    blocked_notice_auto_notify_enabled,
    blocked_notice_auto_notify_marker_enabled,
    blocked_notice_auto_notify_marker_path,
    default_goal_channel_binding_path,
    human_gate_auto_notify_enabled,
    human_gate_auto_notify_marker_enabled,
    human_gate_auto_notify_marker_path,
    now_iso,
    parse_time,
    read_goal_channel_binding,
)
from .goal_channel_transport import CHAT_ID_PATTERN, MESSAGE_ID_PATTERN
from .goal_channel_targets import (
    default_goal_channel_target_path,
    goal_channel_target_for_name,
    read_goal_channel_targets,
)


GOAL_CHANNEL_NOTIFICATION_PROJECTION_SCHEMA_VERSION = (
    "loopx_goal_channel_notification_projection_v0"
)


def _public_text(value: Any, *, limit: int = 120) -> str | None:
    text = compact_goal_channel_text(value, limit=limit)
    if text is None:
        return None
    if CHAT_ID_PATTERN.search(text) or MESSAGE_ID_PATTERN.search(text):
        return None
    return text


def _unconfigured_goal_row(goal_id: str) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "configured": False,
        "enabled": False,
        "human_gate_auto_notify_enabled": False,
        "blocked_notice_auto_notify_enabled": False,
        "receipt_count": 0,
    }


def _read_binding_payload(binding_path: Path) -> Mapping[str, Any]:
    try:
        return read_goal_channel_binding(binding_path)
    except (OSError, ValueError):
        return {}


def _auto_notify_marker_state(binding_path: Path, goal_id: str) -> bool:
    try:
        marker_path = human_gate_auto_notify_marker_path(binding_path, goal_id)
    except ValueError:
        return False
    return human_gate_auto_notify_marker_enabled(marker_path)


def _goal_notification_row(
    *,
    binding_path: Path,
    binding_payload: Mapping[str, Any],
    goal_id: str,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    binding = binding_for_goal(binding_payload, goal_id)
    if not isinstance(binding, Mapping):
        return _unconfigured_goal_row(goal_id)
    row: dict[str, Any] = {
        "goal_id": goal_id,
        "configured": True,
        "enabled": binding.get("enabled") is True,
        "human_gate_auto_notify_enabled": bool(
            human_gate_auto_notify_enabled(binding)
            or _auto_notify_marker_state(binding_path, goal_id)
        ),
        "blocked_notice_auto_notify_enabled": bool(
            blocked_notice_auto_notify_enabled(binding)
            or blocked_notice_auto_notify_marker_enabled(
                blocked_notice_auto_notify_marker_path(binding_path, goal_id)
            )
        ),
    }
    target_ref = _public_text(binding.get("target_ref"))
    if target_ref:
        row["target_ref"] = target_ref
    receipts = binding.get("receipts")
    receipts = receipts if isinstance(receipts, Mapping) else {}
    row["receipt_count"] = len(receipts)
    resolved_binding = binding
    has_notification_receipts = any(
        isinstance(receipt, Mapping) and receipt.get("kind") in {"blocked_notice", "gate_notification"}
        for receipt in receipts.values()
    )
    if binding.get("target_ref") and has_notification_receipts:
        target = None
        try:
            if runtime_root is not None:
                target = goal_channel_target_for_name(
                    read_goal_channel_targets(default_goal_channel_target_path(runtime_root)),
                    str(binding["target_ref"]),
                )
            resolved_binding = (
                binding_for_goal(binding_payload, goal_id, provider_target=target) if target else {}
            )
        except (OSError, ValueError):
            resolved_binding = {}
    channel = resolved_binding.get("channel") if resolved_binding else None
    chat_id = str(channel.get("chat_id") or "") if isinstance(channel, Mapping) else ""
    blocked_receipts = [
        receipt for key, receipt in receipts.items()
        if isinstance(receipt, Mapping)
        and blocked_notice_receipt_matches_target(key, receipt, goal_id=goal_id, chat_id=chat_id)
    ]
    row["blocked_notice_delivery"] = {
        "delivered_count": sum(
            r.get("readback_verified") is True and r.get("state") == BlockedNoticeReceiptState.DELIVERED
            for r in blocked_receipts
        ),
        "unverified_count": sum(
            r.get("state") in {BlockedNoticeReceiptState.PENDING, BlockedNoticeReceiptState.SENT_UNVERIFIED}
            for r in blocked_receipts
        ),
        "resolved_count": sum(r.get("state") in BLOCKED_NOTICE_RETIRED_STATES for r in blocked_receipts),
    }
    from .goal_channel_contracts import semantic_key
    current_notifications = [r for r in receipts.values() if isinstance(r, Mapping)
        and ((r.get("kind") == "gate_notification"
              and r.get("target_generation") == semantic_key("lark_goal_channel_target_v0", chat_id))
             or (r in blocked_receipts and r.get("state") not in BLOCKED_NOTICE_RETIRED_STATES))]
    row["steward_notice_delivery"] = {
        "pending_count": sum(r.get("readback_verified") is not True for r in current_notifications),
        "failed_count": sum(bool(r.get("failure_code")) for r in current_notifications),
    }
    verified: list[tuple[Any, str]] = []
    for key, receipt in receipts.items():
        if not isinstance(receipt, Mapping):
            continue
        if receipt.get("kind") == "blocked_notice" and not blocked_notice_receipt_matches_target(
            key, receipt, goal_id=goal_id, chat_id=chat_id,
        ):
            continue
        moment = parse_time(receipt.get("verified_at"))
        if moment is not None:
            verified.append((moment, str(receipt.get("verified_at"))))
    if verified:
        row["last_notified_at"] = max(verified)[1]
    return row


def build_goal_channel_notification_projection(
    *,
    registry_path: Path,
    registry: Mapping[str, Any] | None = None,
    goal_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Summarize per-goal Lark notification binding state for status.json.

    Every read is best-effort: a missing, unreadable, or corrupt binding file
    degrades that goal (or the whole projection) to an unconfigured row
    instead of raising, so the status hot path never fails on binding state.
    """

    goal_filter = str(goal_id or "").strip() or None
    resolved_registry_path = Path(registry_path).expanduser()
    try:
        resolved_registry_path = resolved_registry_path.resolve()
        registry_payload: Mapping[str, Any] = (
            registry
            if isinstance(registry, Mapping)
            else load_registry(resolved_registry_path)
        )
    except (OSError, ValueError):
        registry_payload = {}

    binding_cache: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for goal in registry_goals(dict(registry_payload)):
        current_goal_id = str(goal.get("id") or "").strip()
        if not current_goal_id or (goal_filter and current_goal_id != goal_filter):
            continue
        row: dict[str, Any] | None = None
        try:
            route = resolve_goal_source_runtime_route(
                registry_path=resolved_registry_path,
                goal_id=current_goal_id,
                registry=dict(registry_payload),
            )
            source_registry_path = Path(str(route["source_registry"]))
            if source_registry_path.parent.name == ".loopx":
                binding_path = default_goal_channel_binding_path(source_registry_path)
                cache_key = str(binding_path)
                if cache_key not in binding_cache:
                    binding_cache[cache_key] = (
                        binding_path,
                        _read_binding_payload(binding_path),
                    )
                cached_path, cached_payload = binding_cache[cache_key]
                row = _goal_notification_row(
                    binding_path=cached_path,
                    binding_payload=cached_payload,
                    goal_id=current_goal_id,
                    runtime_root=(Path(str(route["source_runtime_root"])) if route.get("source_runtime_root") else None),
                )
        except (OSError, ValueError):
            row = None
        rows.append(row if row is not None else _unconfigured_goal_row(current_goal_id))

    projection = {
        "schema_version": GOAL_CHANNEL_NOTIFICATION_PROJECTION_SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "goals": rows,
    }
    assert_public_packet(projection)
    return projection
