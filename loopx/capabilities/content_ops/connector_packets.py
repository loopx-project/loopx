from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schemas import (
    CONNECTOR_TRIAL_SCHEMA_VERSION,
    CONTENT_OPS_PRIVATE_CONNECTOR_GATE_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_PUBLIC_HANDLE_OBSERVATION_PACKET_SCHEMA_VERSION,
)


def _require_packet_flag(payload: Mapping[str, Any], key: str, expected: Any) -> None:
    if payload.get(key) != expected:
        raise ValueError(f"packet {key} must be {expected!r}")


def _connector_trial_from_public_packet(
    packet: Mapping[str, Any],
    source_item: Mapping[str, Any],
    runtime_policy: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    raw_observation = packet.get("observation")
    observation = raw_observation if isinstance(raw_observation, Mapping) else {}
    surface = str(packet.get("surface") or observation.get("surface") or "public_feed")
    source_item_id = str(source_item.get("source_item_id") or "").strip()
    if not source_item_id:
        raise ValueError("public handle packet source_item_id is required")
    return {
        "schema_version": CONNECTOR_TRIAL_SCHEMA_VERSION,
        "trial_id": f"trial_public_packet_{index + 1}",
        "surface": surface,
        "tool_hint": str(
            runtime_policy.get("connector_name") or "public metadata connector"
        ),
        "access_mode": "public_metadata_only",
        "source_status": str(source_item.get("source_status") or "public"),
        "freshness": str(source_item.get("freshness") or "unknown"),
        "allowed_use": str(source_item.get("allowed_use") or "metadata_only"),
        "trial_state": "metadata_packet_collected",
        "proposed_source_item_id": source_item_id,
        "terms_note": str(
            source_item.get("terms_note")
            or "metadata-only public source packet already collected"
        ),
        "promotion_target": "source_item_v0",
        "requires_user_gate": False,
        "external_write_allowed": False,
    }


def _connector_trial_from_private_gate_packet(
    packet: Mapping[str, Any],
    source_item: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    raw_connector = packet.get("connector")
    connector = raw_connector if isinstance(raw_connector, Mapping) else {}
    source_item_id = str(source_item.get("source_item_id") or "").strip()
    if not source_item_id:
        raise ValueError("private connector gate source_item_id is required")
    return {
        "schema_version": CONNECTOR_TRIAL_SCHEMA_VERSION,
        "trial_id": f"trial_private_gate_packet_{index + 1}",
        "surface": str(
            packet.get("surface") or connector.get("surface") or "private_archive"
        ),
        "tool_hint": str(
            connector.get("connector_name") or "private metadata connector"
        ),
        "access_mode": "private_metadata_only",
        "source_status": "private_needs_review",
        "freshness": str(source_item.get("freshness") or "unknown"),
        "allowed_use": "metadata_only",
        "trial_state": "needs_owner_gate",
        "proposed_source_item_id": source_item_id,
        "terms_note": str(
            source_item.get("terms_note")
            or "private connector metadata remains gated before source use"
        ),
        "promotion_target": "source_item_v0_after_owner_gate",
        "requires_user_gate": True,
        "external_write_allowed": False,
    }


def source_item_and_trial_from_public_packet(
    packet: Mapping[str, Any],
    index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        packet.get("schema_version")
        != CONTENT_OPS_PUBLIC_HANDLE_OBSERVATION_PACKET_SCHEMA_VERSION
    ):
        raise ValueError(
            "public packet schema_version must be "
            "content_ops_public_handle_observation_packet_v0"
        )
    _require_packet_flag(packet, "ok", True)
    _require_packet_flag(packet, "external_writes_performed", False)
    _require_packet_flag(packet, "private_source_content_read", False)
    _require_packet_flag(packet, "autopublish_allowed", False)
    raw_runtime_policy = packet.get("runtime_policy")
    runtime_policy = raw_runtime_policy if isinstance(raw_runtime_policy, Mapping) else {}
    if runtime_policy.get("safe_default") != "head_only_metadata_probe":
        raise ValueError("public packet runtime policy must be HEAD-only")
    if runtime_policy.get("browser_open_allowed_before_gate") is not False:
        raise ValueError("public packet must not allow browser open before gate")
    source_item = packet.get("source_item")
    if not isinstance(source_item, Mapping):
        raise ValueError("public packet must include source_item")
    return dict(source_item), _connector_trial_from_public_packet(
        packet,
        source_item,
        runtime_policy,
        index,
    )


def source_item_and_trial_from_private_gate_packet(
    packet: Mapping[str, Any],
    index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        packet.get("schema_version")
        != CONTENT_OPS_PRIVATE_CONNECTOR_GATE_PACKET_SCHEMA_VERSION
    ):
        raise ValueError(
            "private gate packet schema_version must be "
            "content_ops_private_connector_gate_packet_v0"
        )
    _require_packet_flag(packet, "ok", True)
    _require_packet_flag(packet, "owner_gate_required", True)
    _require_packet_flag(packet, "external_reads_performed", False)
    _require_packet_flag(packet, "external_writes_performed", False)
    _require_packet_flag(packet, "private_source_content_read", False)
    _require_packet_flag(packet, "autopublish_allowed", False)
    raw_runtime_policy = packet.get("runtime_policy")
    runtime_policy = raw_runtime_policy if isinstance(raw_runtime_policy, Mapping) else {}
    if runtime_policy.get("safe_default") != "gate_projection_only":
        raise ValueError("private packet runtime policy must be gate-only")
    if runtime_policy.get("browser_open_allowed_before_gate") is not False:
        raise ValueError("private packet must not allow browser open before gate")
    source_item = packet.get("source_item")
    if not isinstance(source_item, Mapping):
        raise ValueError("private gate packet must include source_item")
    return dict(source_item), _connector_trial_from_private_gate_packet(
        packet,
        source_item,
        index,
    )
