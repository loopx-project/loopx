from __future__ import annotations

import ipaddress
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from loopx.control_plane.runtime.time import now_utc_iso

from .connector_packets import (
    source_item_and_trial_from_private_gate_packet,
    source_item_and_trial_from_public_packet,
)
from .social_browser_x import build_social_browser_x_provider_packet
from .markdown import (
    render_content_ops_chatview_report_markdown as render_content_ops_chatview_report_markdown,
    render_content_ops_exploration_plan_markdown as render_content_ops_exploration_plan_markdown,
    render_content_ops_packet_aggregation_markdown as render_content_ops_packet_aggregation_markdown,
    render_content_ops_preview_markdown as render_content_ops_preview_markdown,
    render_content_ops_private_connector_gate_markdown as render_content_ops_private_connector_gate_markdown,
    render_content_ops_public_handle_observation_markdown as render_content_ops_public_handle_observation_markdown,
    render_content_ops_walkthrough_artifact_markdown as render_content_ops_walkthrough_artifact_markdown,
)
from .schemas import (
    ANGLE_CANDIDATE_SCHEMA_VERSION,
    CONNECTOR_TRIAL_SCHEMA_VERSION,
    CONTENT_OPS_CHATVIEW_CONNECTOR_REPORT_SCHEMA_VERSION,
    CONTENT_OPS_CONNECTOR_RUNTIME_POLICY_SCHEMA_VERSION,
    CONTENT_OPS_EXPLORATION_PLAN_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_PACKET_AGGREGATION_SCHEMA_VERSION,
    CONTENT_OPS_PREVIEW_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_PRIVATE_CONNECTOR_GATE_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_PRIVATE_CONNECTOR_OWNER_GATE_SCHEMA_VERSION,
    CONTENT_OPS_PUBLIC_HANDLE_OBSERVATION_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_PUBLIC_HANDLE_OBSERVATION_SCHEMA_VERSION,
    CONTENT_OPS_SURFACE_PROJECTION_SCHEMA_VERSION,
    CONTENT_OPS_SURFACE_SCHEMA_VERSION,
    CONTENT_OPS_VALIDATION_SCHEMA_VERSION,
    CONTENT_OPS_WALKTHROUGH_ARTIFACT_SCHEMA_VERSION,
    DRAFT_ITEM_SCHEMA_VERSION,
    EXPLORATION_PLAN_SCHEMA_VERSION,
    FEEDBACK_SIGNAL_SCHEMA_VERSION,
    MATERIAL_MEMORY_SCHEMA_VERSION,
    PUBLISH_GATE_SCHEMA_VERSION,
    SOURCE_ITEM_SCHEMA_VERSION,
)

RAW_MATERIAL_KEY_HINTS = (
    "body",
    "chat",
    "credential",
    "dm",
    "local_path",
    "log",
    "message",
    "raw",
    "secret",
    "token",
    "transcript",
)

ALLOWED_SOURCE_STATUSES = {
    "public",
    "private_needs_review",
    "synthetic_public_safe",
    "unpublished",
    "forbidden_for_public_surface",
}
ALLOWED_FRESHNESS = {"fresh", "stale", "unknown"}
ALLOWED_USE_POLICIES = {
    "summarize_and_transform",
    "metadata_only",
    "do_not_quote",
    "forbidden",
}
ALLOWED_ANGLE_DECISIONS = {"draft", "reject", "hold", "needs_review"}
ALLOWED_DRAFT_STATES = {"outline", "draft", "rewrite", "blocked", "ready_for_review"}
ALLOWED_FEEDBACK_EFFECTS = {
    "preference_hint",
    "source_boundary_correction",
    "rewrite_todo",
    "publish_decision",
}
ALLOWED_PUBLISH_GATE_STATUSES = {
    "blocked_until_user_approval",
    "approved",
    "denied",
    "needs_revision",
}
ALLOWED_CONNECTOR_TRIAL_STATES = {
    "candidate",
    "metadata_packet_collected",
    "ready_for_metadata_trial",
    "needs_owner_gate",
    "blocked",
}
ALLOWED_CONNECTOR_ACCESS_MODES = {
    "public_metadata_only",
    "private_metadata_only",
    "synthetic_fixture_only",
}
ALLOWED_EXPLORATION_READ_STATUSES = {
    "metadata_ready",
    "not_read",
    "blocked_until_owner_gate",
    "compact_result_ready",
}
ALLOWED_EXPLORATION_EVIDENCE_QUALITIES = {
    "primary_source_metadata",
    "metadata_only",
    "not_evidence_until_approved",
    "compact_result_metadata",
}


def _as_mappings(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not values:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _text(value: Any, *, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _ids(items: Sequence[Mapping[str, Any]], key: str) -> set[str]:
    return {
        str(item.get(key))
        for item in items
        if item.get(key) is not None and str(item.get(key)).strip()
    }


def _counter(values: Sequence[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value).items()))


def _normalise_exploration_label(value: Any, label: str) -> str:
    text = _text(value, limit=80)
    if not text:
        raise ValueError(f"{label} is required")
    lowered = text.lower()
    if (
        "http://" in lowered
        or "https://" in lowered
        or "/users/" in lowered
        or "/private/" in lowered
        or "bearer " in lowered
        or "credential" in lowered
        or "secret" in lowered
    ):
        raise ValueError(f"{label} must be a compact public-safe label")
    return text


def _normalise_public_https_url(url: str) -> tuple[str, Any]:
    text = str(url or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https":
        raise ValueError("public handle observation requires an https URL")
    if parsed.username or parsed.password:
        raise ValueError("public handle URL must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("public handle URL must not include query or fragment data")

    host = parsed.hostname
    if not host:
        raise ValueError("public handle URL must include a host")
    lowered_host = host.lower().rstrip(".")
    if lowered_host in {"localhost"} or lowered_host.endswith((".localhost", ".local")):
        raise ValueError("public handle URL must not target localhost or local hosts")
    if parsed.port not in (None, 443):
        raise ValueError("public handle URL must use the default https port")

    try:
        address = ipaddress.ip_address(lowered_host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("public handle URL must not target private or local addresses")

    path = parsed.path or "/"
    normalised = urlunsplit(("https", parsed.netloc, path, "", ""))
    return normalised, parsed._replace(path=path, query="", fragment="")


class _NoFollowPublicHandleRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _normalise_public_https_url(newurl)
        return None


def _public_handle_attribution(parsed: Any) -> str:
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.hostname}{path}"


def _source_item_from_public_handle_observation(
    *,
    source_item_id: str,
    source_kind: str,
    freshness: str,
    terms_note: str,
    parsed_url: Any,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_ITEM_SCHEMA_VERSION,
        "source_item_id": source_item_id,
        "source_kind": source_kind,
        "source_status": "public",
        "freshness": freshness,
        "terms_note": terms_note,
        "allowed_use": "metadata_only",
        "attribution": _public_handle_attribution(parsed_url),
        "summary": (
            "Public handle observed as metadata-only; no source content was "
            "captured and no external write was attempted."
        ),
        "observation": dict(observation),
    }


def _source_item_from_private_connector_gate(
    *,
    source_item_id: str,
    source_kind: str,
    freshness: str,
    connector_name: str,
    owner_label: str,
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_ITEM_SCHEMA_VERSION,
        "source_item_id": source_item_id,
        "source_kind": source_kind,
        "source_status": "private_needs_review",
        "freshness": freshness,
        "terms_note": (
            "private connector metadata-only placeholder; owner approval is "
            "required before any source content read, quote, summary, or publication"
        ),
        "allowed_use": "metadata_only",
        "attribution": f"{owner_label} via {connector_name}",
        "summary": (
            "Private connector is represented only as an owner-gated metadata "
            "signal; no source content was read or copied."
        ),
        "owner_gate": dict(gate),
    }


def build_content_ops_connector_runtime_policy(
    *,
    connector_id: str,
    connector_name: str,
    access_mode: str,
    connector_url: str | None = None,
) -> dict[str, Any]:
    """Build the runtime policy that keeps connector runs inside source bounds."""

    if access_mode not in ALLOWED_CONNECTOR_ACCESS_MODES:
        allowed_modes = sorted(ALLOWED_CONNECTOR_ACCESS_MODES)
        raise ValueError(f"access_mode must be one of {allowed_modes}")

    if access_mode == "public_metadata_only":
        return {
            "schema_version": CONTENT_OPS_CONNECTOR_RUNTIME_POLICY_SCHEMA_VERSION,
            "connector_id": connector_id,
            "connector_name": connector_name,
            "access_mode": access_mode,
            "safe_default": "head_only_metadata_probe",
            "browser_open_allowed_before_gate": False,
            "browser_open_risk": (
                "public browser pages can autoload timelines, post text, media, "
                "video streams, analytics, and engagement counters"
            ),
            "allowed_probe_methods": ["HEAD"],
            "allowed_before_approval": [
                "verify URL and host",
                "read response status and content-type headers",
                "record attribution and freshness metadata",
            ],
            "forbidden_before_approval": [
                "timeline body capture",
                "media download",
                "login-gated reads",
                "posting or engagement actions",
            ],
        }

    if access_mode == "private_metadata_only":
        return {
            "schema_version": CONTENT_OPS_CONNECTOR_RUNTIME_POLICY_SCHEMA_VERSION,
            "connector_id": connector_id,
            "connector_name": connector_name,
            "access_mode": access_mode,
            "connector_url": connector_url,
            "safe_default": "gate_projection_only",
            "browser_open_allowed_before_gate": False,
            "browser_open_risk": (
                "the default web app route may autoload private-source message "
                "lists or message-detail APIs before an agent can intervene"
            ),
            "allowed_probe_methods": [],
            "allowed_before_approval": [
                "store this compact gate packet",
                "display the owner question",
                "prepare fixture-only smoke coverage",
            ],
            "forbidden_url_path_prefixes_before_approval": [
                "/api/messages",
                "/api/reports",
                "/api/channel-state",
            ],
            "forbidden_before_approval": [
                "browser-opening the default private connector route",
                "private source content read",
                "message-list API calls",
                "message-detail API calls",
                "derived report ingestion",
                "source quote",
                "source summary",
                "external posting",
                "autopublish",
            ],
        }

    return {
        "schema_version": CONTENT_OPS_CONNECTOR_RUNTIME_POLICY_SCHEMA_VERSION,
        "connector_id": connector_id,
        "connector_name": connector_name,
        "access_mode": access_mode,
        "safe_default": "fixture_only",
        "browser_open_allowed_before_gate": False,
        "allowed_before_approval": ["fixture-only validation"],
        "forbidden_before_approval": ["external reads", "external writes"],
    }


def _raw_material_key_names(*groups: Sequence[Mapping[str, Any]]) -> list[str]:
    names: set[str] = set()
    for group in groups:
        for item in group:
            for key in item:
                lowered = str(key).lower()
                if any(hint in lowered for hint in RAW_MATERIAL_KEY_HINTS):
                    names.add(str(key))
    return sorted(names)


def build_content_ops_surface_fixture(
    *, generated_at: str | None = "2026-06-23T00:00:00Z"
) -> dict[str, Any]:
    """Build a synthetic public-safe content-ops state surface fixture.

    This fixture demonstrates the shape of a creator/self-media loop without
    copying raw platform posts, chat messages, draft bodies, credentials, or
    local paths into LoopX state.
    """

    source_items = [
        {
            "schema_version": SOURCE_ITEM_SCHEMA_VERSION,
            "source_item_id": "source_demo_public_feed_001",
            "source_kind": "synthetic_demo_feed",
            "source_status": "synthetic_public_safe",
            "freshness": "fresh",
            "terms_note": "synthetic demo only; no platform scraping claim",
            "allowed_use": "summarize_and_transform",
            "attribution": "LoopX synthetic creator-ops demo",
            "summary": (
                "A public-safe trend summary suggests creator operators need "
                "source-aware drafting queues."
            ),
        },
        {
            "schema_version": SOURCE_ITEM_SCHEMA_VERSION,
            "source_item_id": "source_demo_private_note_001",
            "source_kind": "synthetic_private_note",
            "source_status": "private_needs_review",
            "freshness": "fresh",
            "terms_note": "metadata-only placeholder for private material",
            "allowed_use": "metadata_only",
            "attribution": "operator-owned private source placeholder",
            "summary": "Private source is represented only as a compact review-needed signal.",
        },
    ]
    angle_candidates = [
        {
            "schema_version": ANGLE_CANDIDATE_SCHEMA_VERSION,
            "angle_id": "angle_source_aware_loop",
            "source_item_ids": ["source_demo_public_feed_001"],
            "audience": "maintainers evaluating creator-ops automation",
            "topic": "source-aware drafting loop",
            "novelty": "connects connector observations to explicit publish gates",
            "preference_fit": "high",
            "evidence_quality": "synthetic_demo",
            "decision": "draft",
        },
        {
            "schema_version": ANGLE_CANDIDATE_SCHEMA_VERSION,
            "angle_id": "angle_private_material_quote",
            "source_item_ids": ["source_demo_private_note_001"],
            "audience": "same",
            "topic": "private source quote",
            "novelty": "blocked by source boundary",
            "preference_fit": "unknown",
            "evidence_quality": "needs_owner_review",
            "decision": "reject",
            "rejection_reason": "private material cannot be quoted or promoted without review",
        },
    ]
    draft_items = [
        {
            "schema_version": DRAFT_ITEM_SCHEMA_VERSION,
            "draft_id": "draft_source_aware_loop_outline",
            "angle_id": "angle_source_aware_loop",
            "state": "outline",
            "source_map": [
                {
                    "source_item_id": "source_demo_public_feed_001",
                    "use": "summarized premise",
                }
            ],
            "preference_hints": [
                "explain value as quality and feedback, not raw publish count",
                "keep publish decision human-gated",
            ],
            "publish_gate_id": "publish_gate_source_aware_loop",
            "validation_surface": (
                "source map present; no raw private material; publish gate visible"
            ),
        }
    ]
    feedback_signals = [
        {
            "schema_version": FEEDBACK_SIGNAL_SCHEMA_VERSION,
            "feedback_id": "feedback_demo_style_001",
            "target_id": "draft_source_aware_loop_outline",
            "signal": "useful_but_less_salesy",
            "effect": "preference_hint",
            "writes_todo": False,
            "summary": "Favor operator-quality framing over content volume claims.",
        },
        {
            "schema_version": FEEDBACK_SIGNAL_SCHEMA_VERSION,
            "feedback_id": "feedback_private_source_boundary_001",
            "target_id": "source_demo_private_note_001",
            "signal": "do_not_use_source_body",
            "effect": "source_boundary_correction",
            "writes_todo": False,
            "summary": "Private source stays metadata-only until an explicit review approves use.",
        },
    ]
    publish_gates = [
        {
            "schema_version": PUBLISH_GATE_SCHEMA_VERSION,
            "gate_id": "publish_gate_source_aware_loop",
            "draft_id": "draft_source_aware_loop_outline",
            "status": "blocked_until_user_approval",
            "approval_required": True,
            "autopublish_allowed": False,
            "required_review": [
                "source attribution",
                "tone/style",
                "platform policy",
                "final publish destination",
            ],
        }
    ]
    material_memory = [
        {
            "schema_version": MATERIAL_MEMORY_SCHEMA_VERSION,
            "memory_id": "memory_source_aware_loop",
            "source_item_id": "source_demo_public_feed_001",
            "attribution": "LoopX synthetic creator-ops demo",
            "reuse_boundary": "demo_only",
            "rejected_angles": ["angle_private_material_quote"],
            "preference_hints": ["quality and feedback beat raw article count"],
        }
    ]
    connector_trials = [
        build_social_browser_x_provider_packet()["connector_trial"],
        {
            "schema_version": CONNECTOR_TRIAL_SCHEMA_VERSION,
            "trial_id": "trial_wechat_chatlog_alpha",
            "surface": "wechat_private_archive",
            "tool_hint": "chatlog-alpha/chatview",
            "access_mode": "private_metadata_only",
            "source_status": "private_needs_review",
            "freshness": "unknown",
            "allowed_use": "metadata_only",
            "trial_state": "needs_owner_gate",
            "proposed_source_item_id": "source_wechat_metadata_signal_001",
            "terms_note": "private material intake stays metadata-only until owner review approves any use",
            "promotion_target": "source_item_v0",
            "requires_user_gate": True,
            "external_write_allowed": False,
        },
    ]
    return {
        "schema_version": CONTENT_OPS_SURFACE_SCHEMA_VERSION,
        "surface_id": "creator_ops_public_safe_demo",
        "generated_at": generated_at,
        "mode": "compact_state_surface",
        "source_items": source_items,
        "angle_candidates": angle_candidates,
        "draft_items": draft_items,
        "feedback_signals": feedback_signals,
        "publish_gates": publish_gates,
        "material_memory": material_memory,
        "connector_trials": connector_trials,
        "operator_states": [
            "waiting_for_source_review",
            "ready_to_draft",
            "waiting_for_feedback",
            "ready_for_publish_decision",
            "safe_side_work_available",
        ],
        "boundary": {
            "public_safe": True,
            "raw_private_material_recorded": False,
            "raw_platform_data_recorded": False,
            "credentials_recorded": False,
            "autopublish_allowed": False,
            "publish_requires_user_gate": True,
            "connector_bodies_are_source_of_truth": False,
        },
    }


def validate_content_ops_surface(surface: Mapping[str, Any]) -> dict[str, Any]:
    source_items = _as_mappings(surface.get("source_items"))  # type: ignore[arg-type]
    angle_candidates = _as_mappings(surface.get("angle_candidates"))  # type: ignore[arg-type]
    draft_items = _as_mappings(surface.get("draft_items"))  # type: ignore[arg-type]
    feedback_signals = _as_mappings(surface.get("feedback_signals"))  # type: ignore[arg-type]
    publish_gates = _as_mappings(surface.get("publish_gates"))  # type: ignore[arg-type]
    material_memory = _as_mappings(surface.get("material_memory"))  # type: ignore[arg-type]
    connector_trials = _as_mappings(surface.get("connector_trials"))  # type: ignore[arg-type]

    errors: list[str] = []
    source_ids = _ids(source_items, "source_item_id")
    angle_ids = _ids(angle_candidates, "angle_id")
    draft_ids = _ids(draft_items, "draft_id")
    gate_ids = _ids(publish_gates, "gate_id")

    if surface.get("schema_version") != CONTENT_OPS_SURFACE_SCHEMA_VERSION:
        errors.append("surface schema_version must be content_ops_surface_v0")
    if not source_items:
        errors.append("at least one source_item_v0 record is required")
    if not angle_candidates:
        errors.append("at least one angle_candidate_v0 record is required")
    if not draft_items:
        errors.append("at least one draft_item_v0 record is required")
    if not feedback_signals:
        errors.append("at least one feedback_signal_v0 record is required")
    if not publish_gates:
        errors.append("at least one publish_gate_v0 record is required")
    if not material_memory:
        errors.append("at least one material_memory_v0 record is required")
    if not connector_trials:
        errors.append("at least one connector_trial_v0 record is required")

    for item in source_items:
        if item.get("schema_version") != SOURCE_ITEM_SCHEMA_VERSION:
            errors.append(f"source item {item.get('source_item_id')} has wrong schema")
        if item.get("source_status") not in ALLOWED_SOURCE_STATUSES:
            errors.append(
                f"source item {item.get('source_item_id')} has invalid source_status"
            )
        if item.get("freshness") not in ALLOWED_FRESHNESS:
            errors.append(f"source item {item.get('source_item_id')} has invalid freshness")
        if item.get("allowed_use") not in ALLOWED_USE_POLICIES:
            errors.append(f"source item {item.get('source_item_id')} has invalid allowed_use")

    for item in angle_candidates:
        if item.get("schema_version") != ANGLE_CANDIDATE_SCHEMA_VERSION:
            errors.append(f"angle {item.get('angle_id')} has wrong schema")
        if item.get("decision") not in ALLOWED_ANGLE_DECISIONS:
            errors.append(f"angle {item.get('angle_id')} has invalid decision")
        for source_id in item.get("source_item_ids") or []:
            if str(source_id) not in source_ids:
                errors.append(
                    f"angle {item.get('angle_id')} references unknown source {source_id}"
                )

    for item in draft_items:
        if item.get("schema_version") != DRAFT_ITEM_SCHEMA_VERSION:
            errors.append(f"draft {item.get('draft_id')} has wrong schema")
        if item.get("state") not in ALLOWED_DRAFT_STATES:
            errors.append(f"draft {item.get('draft_id')} has invalid state")
        if str(item.get("angle_id")) not in angle_ids:
            errors.append(f"draft {item.get('draft_id')} references unknown angle")
        if str(item.get("publish_gate_id")) not in gate_ids:
            errors.append(f"draft {item.get('draft_id')} references unknown publish gate")
        source_map = item.get("source_map")
        if not isinstance(source_map, Sequence) or isinstance(source_map, (str, bytes)):
            errors.append(f"draft {item.get('draft_id')} must carry a source_map")
        else:
            for source_ref in source_map:
                if not isinstance(source_ref, Mapping):
                    errors.append(f"draft {item.get('draft_id')} has invalid source_map item")
                    continue
                source_id = str(source_ref.get("source_item_id") or "")
                if source_id not in source_ids:
                    errors.append(
                        f"draft {item.get('draft_id')} source_map references unknown source"
                    )

    for item in feedback_signals:
        if item.get("schema_version") != FEEDBACK_SIGNAL_SCHEMA_VERSION:
            errors.append(f"feedback {item.get('feedback_id')} has wrong schema")
        if item.get("effect") not in ALLOWED_FEEDBACK_EFFECTS:
            errors.append(f"feedback {item.get('feedback_id')} has invalid effect")
        target_id = str(item.get("target_id") or "")
        if (
            target_id not in draft_ids
            and target_id not in source_ids
            and target_id not in angle_ids
        ):
            errors.append(f"feedback {item.get('feedback_id')} references unknown target")

    for item in publish_gates:
        if item.get("schema_version") != PUBLISH_GATE_SCHEMA_VERSION:
            errors.append(f"publish gate {item.get('gate_id')} has wrong schema")
        if item.get("status") not in ALLOWED_PUBLISH_GATE_STATUSES:
            errors.append(f"publish gate {item.get('gate_id')} has invalid status")
        if item.get("autopublish_allowed") is not False:
            errors.append(
                f"publish gate {item.get('gate_id')} must set autopublish_allowed=false"
            )
        if item.get("approval_required") is not True:
            errors.append(f"publish gate {item.get('gate_id')} must require approval")

    for item in material_memory:
        if item.get("schema_version") != MATERIAL_MEMORY_SCHEMA_VERSION:
            errors.append(f"memory {item.get('memory_id')} has wrong schema")
        source_id = str(item.get("source_item_id") or "")
        if source_id not in source_ids:
            errors.append(f"memory {item.get('memory_id')} references unknown source")

    for item in connector_trials:
        if item.get("schema_version") != CONNECTOR_TRIAL_SCHEMA_VERSION:
            errors.append(f"connector trial {item.get('trial_id')} has wrong schema")
        if item.get("source_status") not in ALLOWED_SOURCE_STATUSES:
            errors.append(
                f"connector trial {item.get('trial_id')} has invalid source_status"
            )
        if item.get("freshness") not in ALLOWED_FRESHNESS:
            errors.append(
                f"connector trial {item.get('trial_id')} has invalid freshness"
            )
        if item.get("allowed_use") not in ALLOWED_USE_POLICIES:
            errors.append(
                f"connector trial {item.get('trial_id')} has invalid allowed_use"
            )
        if item.get("trial_state") not in ALLOWED_CONNECTOR_TRIAL_STATES:
            errors.append(
                f"connector trial {item.get('trial_id')} has invalid trial_state"
            )
        if item.get("access_mode") not in ALLOWED_CONNECTOR_ACCESS_MODES:
            errors.append(
                f"connector trial {item.get('trial_id')} has invalid access_mode"
            )
        if item.get("external_write_allowed") is not False:
            errors.append(
                f"connector trial {item.get('trial_id')} must keep external_write_allowed=false"
            )
        if item.get("access_mode") == "private_metadata_only" and item.get(
            "requires_user_gate"
        ) is not True:
            errors.append(
                f"connector trial {item.get('trial_id')} must gate private metadata use"
            )

    boundary = surface.get("boundary") if isinstance(surface.get("boundary"), Mapping) else {}
    if boundary.get("public_safe") is not True:
        errors.append("boundary.public_safe must be true")
    for key in (
        "raw_private_material_recorded",
        "raw_platform_data_recorded",
        "credentials_recorded",
        "autopublish_allowed",
        "connector_bodies_are_source_of_truth",
    ):
        if boundary.get(key) is not False:
            errors.append(f"boundary.{key} must be false")
    if boundary.get("publish_requires_user_gate") is not True:
        errors.append("boundary.publish_requires_user_gate must be true")

    raw_key_names = _raw_material_key_names(
        source_items,
        angle_candidates,
        draft_items,
        feedback_signals,
        publish_gates,
        material_memory,
        connector_trials,
    )
    if raw_key_names:
        errors.append(
            "raw/private-looking key names must not appear in content-ops records"
        )

    return {
        "schema_version": CONTENT_OPS_VALIDATION_SCHEMA_VERSION,
        "ok": not errors,
        "errors": errors,
        "record_counts": {
            "source_items": len(source_items),
            "angle_candidates": len(angle_candidates),
            "draft_items": len(draft_items),
            "feedback_signals": len(feedback_signals),
            "publish_gates": len(publish_gates),
            "material_memory": len(material_memory),
            "connector_trials": len(connector_trials),
        },
        "raw_material_key_names": raw_key_names,
    }


def project_content_ops_surface(surface: Mapping[str, Any]) -> dict[str, Any]:
    """Project a content-ops surface into first-screen status fields."""

    source_items = _as_mappings(surface.get("source_items"))  # type: ignore[arg-type]
    angle_candidates = _as_mappings(surface.get("angle_candidates"))  # type: ignore[arg-type]
    draft_items = _as_mappings(surface.get("draft_items"))  # type: ignore[arg-type]
    feedback_signals = _as_mappings(surface.get("feedback_signals"))  # type: ignore[arg-type]
    publish_gates = _as_mappings(surface.get("publish_gates"))  # type: ignore[arg-type]
    material_memory = _as_mappings(surface.get("material_memory"))  # type: ignore[arg-type]
    connector_trials = _as_mappings(surface.get("connector_trials"))  # type: ignore[arg-type]
    validation = validate_content_ops_surface(surface)

    source_review_required = [
        item
        for item in source_items
        if item.get("source_status") in {"private_needs_review", "unpublished"}
        or item.get("allowed_use") == "metadata_only"
    ]
    ready_angles = [
        item for item in angle_candidates if item.get("decision") == "draft"
    ]
    drafts_waiting_feedback = [
        item
        for item in draft_items
        if item.get("state") in {"outline", "draft", "ready_for_review"}
    ]
    publish_decision_gates = [
        item
        for item in publish_gates
        if item.get("status") == "blocked_until_user_approval"
    ]
    feedback_effects = _counter(item.get("effect") for item in feedback_signals)
    operator_states = [
        str(item)
        for item in surface.get("operator_states", []) or []
        if isinstance(item, str) and item.strip()
    ]
    user_action_required = bool(publish_decision_gates)
    safe_side_work_available = "safe_side_work_available" in operator_states
    ready_to_draft = bool(ready_angles)

    if user_action_required:
        waiting_on = "user"
        next_safe_action = "review source map and publish gate before external posting"
    elif ready_to_draft:
        waiting_on = "agent"
        next_safe_action = "draft or rewrite from approved source-mapped angle"
    elif source_review_required:
        waiting_on = "operator"
        next_safe_action = "review source status before drafting"
    else:
        waiting_on = "agent"
        next_safe_action = "collect more compact source signals"

    todo_candidates = []
    if ready_angles:
        todo_candidates.append(
            {
                "role": "agent",
                "action_kind": "content_ops_draft_from_angle",
                "title": "Draft or rewrite the selected source-mapped angle",
                "angle_ids": [str(item.get("angle_id")) for item in ready_angles],
                "validation_surface": "source_map plus publish_gate must remain present",
                "stop_condition": "stop before external posting",
            }
        )
    if source_review_required:
        todo_candidates.append(
            {
                "role": "user",
                "action_kind": "content_ops_source_review",
                "title": "Review private or metadata-only source before use",
                "source_item_ids": [
                    str(item.get("source_item_id")) for item in source_review_required
                ],
                "validation_surface": "source_status and allowed_use updated",
            }
        )
    if publish_decision_gates:
        todo_candidates.append(
            {
                "role": "user",
                "action_kind": "content_ops_publish_gate",
                "title": "Approve, deny, or request revision before publication",
                "publish_gate_ids": [
                    str(item.get("gate_id")) for item in publish_decision_gates
                ],
                "validation_surface": "publish gate decision recorded",
            }
        )
    runnable_connector_trials = [
        item
        for item in connector_trials
        if item.get("trial_state") == "ready_for_metadata_trial"
        and item.get("external_write_allowed") is False
    ]
    gated_connector_trials = [
        item for item in connector_trials if item.get("requires_user_gate") is True
    ]
    if runnable_connector_trials:
        todo_candidates.append(
            {
                "role": "agent",
                "action_kind": "content_ops_connector_metadata_trial",
                "title": "Run a connector metadata-only observation trial",
                "trial_ids": [
                    str(item.get("trial_id")) for item in runnable_connector_trials
                ],
                "validation_surface": (
                    "compact source_item_v0 produced; no raw platform or private material"
                ),
                "stop_condition": "stop before login-gated reads, posting, or private source use",
            }
        )
    if gated_connector_trials:
        todo_candidates.append(
            {
                "role": "user",
                "action_kind": "content_ops_connector_owner_gate",
                "title": "Approve or reject private connector metadata intake",
                "trial_ids": [str(item.get("trial_id")) for item in gated_connector_trials],
                "validation_surface": "connector trial gate decision recorded",
            }
        )

    return {
        "schema_version": CONTENT_OPS_SURFACE_PROJECTION_SCHEMA_VERSION,
        "surface_schema_version": surface.get("schema_version"),
        "surface_id": _text(surface.get("surface_id"), limit=120),
        "mode": "read_only",
        "first_screen": {
            "waiting_on": waiting_on,
            "user_action_required": user_action_required,
            "agent_can_continue": bool(ready_to_draft or safe_side_work_available),
            "safe_side_work_available": safe_side_work_available,
            "source_review_required_count": len(source_review_required),
            "ready_to_draft_count": len(ready_angles),
            "waiting_for_feedback_count": len(drafts_waiting_feedback),
            "publish_decision_count": len(publish_decision_gates),
            "next_safe_action": next_safe_action,
        },
        "record_counts": validation["record_counts"],
        "source_statuses": _counter(item.get("source_status") for item in source_items),
        "draft_states": _counter(item.get("state") for item in draft_items),
        "feedback_effects": feedback_effects,
        "publish_gate_statuses": _counter(item.get("status") for item in publish_gates),
        "connector_trials": {
            "count": len(connector_trials),
            "states": _counter(item.get("trial_state") for item in connector_trials),
            "access_modes": _counter(item.get("access_mode") for item in connector_trials),
            "surfaces": _counter(item.get("surface") for item in connector_trials),
            "ready_for_metadata_trial_count": len(runnable_connector_trials),
            "owner_gate_required_count": len(gated_connector_trials),
        },
        "material_memory": {
            "count": len(material_memory),
            "reuse_boundaries": _counter(
                item.get("reuse_boundary") for item in material_memory
            ),
        },
        "todo_candidates": todo_candidates,
        "validation": validation,
        "truth_contract": {
            "projection_is_writable": False,
            "write_authority": "none",
            "source_surface_is_source_of_truth": True,
            "publish_gate_required": True,
            "autopublish_allowed": False,
            "raw_private_material_copied": False,
            "recompute_rule": (
                "recompute from compact content_ops_surface_v0 records; "
                "do not edit this projection as source state"
            ),
        },
    }


def build_content_ops_preview_packet(
    *, generated_at: str | None = "2026-06-23T00:00:00Z"
) -> dict[str, Any]:
    """Build a public-safe content-ops preview packet.

    The preview uses synthetic/metadata-only connector trial records. It does
    not read platform timelines, private chat archives, credentials, or source
    bodies.
    """

    surface = build_content_ops_surface_fixture(generated_at=generated_at)
    validation = validate_content_ops_surface(surface)
    projection = project_content_ops_surface(surface)
    return {
        "ok": bool(validation.get("ok")),
        "schema_version": CONTENT_OPS_PREVIEW_PACKET_SCHEMA_VERSION,
        "mode": "content-ops-preview",
        "surface": surface,
        "projection": projection,
        "validation": validation,
        "connector_trials": projection.get("connector_trials"),
        "external_reads_performed": False,
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "autopublish_allowed": False,
        "next_safe_action": projection.get("first_screen", {}).get("next_safe_action")
        if isinstance(projection.get("first_screen"), Mapping)
        else None,
    }


def build_content_ops_exploration_plan_packet(
    *,
    scenario: str = "mixed_connector_product_workflow",
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    """Build a fixture-only exploration plan packet.

    The plan expresses source lanes, read status, gates, and promotion targets
    before any connector reads source bodies or performs external writes.
    """

    scenario_label = _normalise_exploration_label(
        scenario,
        "scenario",
    )
    source_lanes = [
        {
            "lane_id": "repo_issue_public_metadata",
            "surface": "repo_issue_fix",
            "source_kind": "github_issue_or_pr",
            "source_status": "public",
            "access_mode": "public_metadata_only",
            "read_status": "metadata_ready",
            "route": "GitHub CLI or issue API metadata read",
            "fallback": "browser permalink metadata or ask for a stable issue URL",
            "evidence_quality": "primary_source_metadata",
            "promotion_target": "loopx_agent_todo_candidate",
            "requires_user_gate": False,
            "source_body_captured": False,
            "response_payload_captured": False,
            "local_path_captured": False,
            "external_write_allowed": False,
        },
        {
            "lane_id": "public_social_signal_metadata",
            "surface": "content_ops_signal_intake",
            "source_kind": "public_social_handle",
            "source_status": "public",
            "access_mode": "public_metadata_only",
            "read_status": "not_read",
            "route": "HEAD-only public handle metadata probe",
            "fallback": "operator-provided public permalink metadata",
            "evidence_quality": "metadata_only",
            "promotion_target": "source_item_v0",
            "requires_user_gate": False,
            "source_body_captured": False,
            "response_payload_captured": False,
            "local_path_captured": False,
            "external_write_allowed": False,
        },
        {
            "lane_id": "private_chat_metadata_gate",
            "surface": "content_ops_private_material",
            "source_kind": "private_chat_connector",
            "source_status": "private_needs_review",
            "access_mode": "private_metadata_only",
            "read_status": "blocked_until_owner_gate",
            "route": "owner-gate projection before connector read",
            "fallback": "owner supplies compact count and topic labels",
            "evidence_quality": "not_evidence_until_approved",
            "promotion_target": "source_item_v0_after_owner_gate",
            "requires_user_gate": True,
            "user_gate": {
                "role": "user",
                "action_kind": "approve_private_metadata_intake",
                "question": (
                    "Approve metadata-only private connector intake, reject it, "
                    "or narrow the source handle before source content use."
                ),
                "blocks": [
                    "source content read",
                    "source quote",
                    "source summary",
                    "external posting",
                ],
            },
            "source_body_captured": False,
            "response_payload_captured": False,
            "local_path_captured": False,
            "external_write_allowed": False,
        },
        {
            "lane_id": "experiment_compact_result_metadata",
            "surface": "experiment_state_surface",
            "source_kind": "ml_experiment_run",
            "source_status": "unpublished",
            "access_mode": "synthetic_fixture_only",
            "read_status": "compact_result_ready",
            "route": "experiment reducer emits compact status counters",
            "fallback": "operator supplies public-safe result card",
            "evidence_quality": "compact_result_metadata",
            "promotion_target": "experiment_state_surface_candidate",
            "requires_user_gate": True,
            "user_gate": {
                "role": "user",
                "action_kind": "approve_raw_experiment_material_use",
                "question": (
                    "Approve any raw experiment material read; compact status "
                    "metadata can be used without raw logs."
                ),
                "blocks": [
                    "raw run log read",
                    "private dataset sample read",
                    "credential or environment dump",
                ],
            },
            "source_body_captured": False,
            "response_payload_captured": False,
            "local_path_captured": False,
            "external_write_allowed": False,
        },
    ]
    validation = validate_content_ops_exploration_plan_lanes(source_lanes)
    user_gate_lanes = [
        lane for lane in source_lanes if bool(lane.get("requires_user_gate"))
    ]
    plan = {
        "schema_version": EXPLORATION_PLAN_SCHEMA_VERSION,
        "scenario": scenario_label,
        "generated_at": generated_at,
        "selected_source_lanes": source_lanes,
        "lane_counts": {
            "total": len(source_lanes),
            "user_gate_required": len(user_gate_lanes),
            "metadata_ready": sum(
                1 for lane in source_lanes if lane.get("read_status") == "metadata_ready"
            ),
            "blocked_until_owner_gate": sum(
                1
                for lane in source_lanes
                if lane.get("read_status") == "blocked_until_owner_gate"
            ),
        },
        "promotion_targets": _counter(
            [lane.get("promotion_target") for lane in source_lanes]
        ),
        "first_screen": {
            "waiting_on": "user" if user_gate_lanes else "agent",
            "user_action_required": bool(user_gate_lanes),
            "agent_can_continue": True,
            "top_agent_action": (
                "promote metadata-ready public lanes or ask concrete owner gates "
                "before private/raw source use"
            ),
            "top_user_gate": user_gate_lanes[0].get("user_gate")
            if user_gate_lanes
            else None,
        },
        "boundary": {
            "external_reads_performed": False,
            "external_writes_performed": False,
            "source_bodies_captured": False,
            "response_payloads_captured": False,
            "local_paths_captured": False,
            "autopublish_allowed": False,
        },
        "truth_contract": {
            "plan_is_writable": False,
            "source_lanes_are_candidates": True,
            "promotion_requires_target_surface_validation": True,
            "private_boundary_crossing_requires_user_gate": True,
        },
    }
    return {
        "ok": bool(validation.get("ok")),
        "schema_version": CONTENT_OPS_EXPLORATION_PLAN_PACKET_SCHEMA_VERSION,
        "mode": "content-ops-exploration-plan",
        "exploration_plan": plan,
        "validation": validation,
        "external_reads_performed": False,
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "private_source_content_read": False,
        "local_paths_captured": False,
        "autopublish_allowed": False,
        "next_safe_action": plan["first_screen"]["top_agent_action"],
    }


def validate_content_ops_exploration_plan_lanes(
    source_lanes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    if not source_lanes:
        errors.append("at least one source lane is required")
    seen_ids: set[str] = set()
    for index, lane in enumerate(source_lanes):
        lane_id = str(lane.get("lane_id") or "").strip()
        if not lane_id:
            errors.append(f"lane {index + 1}: lane_id is required")
        elif lane_id in seen_ids:
            errors.append(f"lane {index + 1}: duplicate lane_id {lane_id}")
        seen_ids.add(lane_id)
        if lane.get("source_status") not in ALLOWED_SOURCE_STATUSES:
            errors.append(
                f"{lane_id or index + 1}: source_status must be one of "
                f"{sorted(ALLOWED_SOURCE_STATUSES)}"
            )
        if lane.get("access_mode") not in ALLOWED_CONNECTOR_ACCESS_MODES:
            errors.append(
                f"{lane_id or index + 1}: access_mode must be one of "
                f"{sorted(ALLOWED_CONNECTOR_ACCESS_MODES)}"
            )
        if lane.get("read_status") not in ALLOWED_EXPLORATION_READ_STATUSES:
            errors.append(
                f"{lane_id or index + 1}: read_status must be one of "
                f"{sorted(ALLOWED_EXPLORATION_READ_STATUSES)}"
            )
        if lane.get("evidence_quality") not in ALLOWED_EXPLORATION_EVIDENCE_QUALITIES:
            errors.append(
                f"{lane_id or index + 1}: evidence_quality must be one of "
                f"{sorted(ALLOWED_EXPLORATION_EVIDENCE_QUALITIES)}"
            )
        if not str(lane.get("route") or "").strip():
            errors.append(f"{lane_id or index + 1}: route is required")
        if not str(lane.get("fallback") or "").strip():
            errors.append(f"{lane_id or index + 1}: fallback is required")
        if not str(lane.get("promotion_target") or "").strip():
            errors.append(f"{lane_id or index + 1}: promotion_target is required")
        if bool(lane.get("source_body_captured")):
            errors.append(f"{lane_id or index + 1}: source_body_captured must be false")
        if bool(lane.get("response_payload_captured")):
            errors.append(
                f"{lane_id or index + 1}: response_payload_captured must be false"
            )
        if bool(lane.get("local_path_captured")):
            errors.append(f"{lane_id or index + 1}: local_path_captured must be false")
        if bool(lane.get("external_write_allowed")):
            errors.append(f"{lane_id or index + 1}: external_write_allowed must be false")
        if bool(lane.get("requires_user_gate")) and not isinstance(
            lane.get("user_gate"), Mapping
        ):
            errors.append(f"{lane_id or index + 1}: user_gate is required")
    return {
        "schema_version": "content_ops_exploration_plan_validation_v0",
        "ok": not errors,
        "errors": errors,
        "lane_count": len(source_lanes),
    }


def build_content_ops_public_handle_observation_packet(
    *,
    url: str,
    source_item_id: str,
    surface: str = "x_public_feed",
    source_kind: str = "x_public_profile_handle",
    freshness: str = "fresh",
    terms_note: str | None = None,
    timeout_seconds: float = 10.0,
    fetch: bool = True,
) -> dict[str, Any]:
    """Observe a public handle as a metadata-only ``source_item_v0`` record.

    The live path issues at most one HEAD request. It never reads response
    content, sends cookies, logs in, posts, or follows redirects.
    """

    if not str(source_item_id or "").strip():
        raise ValueError("source_item_id is required")
    if freshness not in ALLOWED_FRESHNESS:
        raise ValueError(f"freshness must be one of {sorted(ALLOWED_FRESHNESS)}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    normalised_url, parsed_url = _normalise_public_https_url(url)
    effective_terms_note = terms_note or (
        "public metadata-only handle observation; no login, body capture, "
        "posting, or private source use"
    )
    observation: dict[str, Any] = {
        "schema_version": CONTENT_OPS_PUBLIC_HANDLE_OBSERVATION_SCHEMA_VERSION,
        "surface": surface,
        "input_url": normalised_url,
        "url_effective": normalised_url,
        "url_host": parsed_url.hostname,
        "url_path": parsed_url.path,
        "http_method": "HEAD" if fetch else "none",
        "http_status": None,
        "content_type": None,
        "redirect_location": None,
        "content_bytes_read": 0,
        "external_write_performed": False,
        "login_used": False,
        "cookies_sent": False,
        "private_source_content_read": False,
        "autopublish_allowed": False,
    }

    if fetch:
        request = Request(
            normalised_url,
            headers={"User-Agent": "loopx-content-ops-public-handle-metadata/0.1"},
            method="HEAD",
        )
        opener = build_opener(_NoFollowPublicHandleRedirect)
        response = None
        try:
            response = opener.open(request, timeout=timeout_seconds)
            observation["http_status"] = response.getcode()
            observation["url_effective"] = response.geturl()
            observation["content_type"] = response.headers.get("Content-Type")
        except HTTPError as exc:
            observation["http_status"] = exc.code
            observation["url_effective"] = exc.geturl()
            observation["content_type"] = exc.headers.get("Content-Type")
            location = exc.headers.get("Location")
            if location:
                redirect_url = urljoin(normalised_url, location)
                _normalise_public_https_url(redirect_url)
                observation["redirect_location"] = redirect_url
        except URLError as exc:
            raise RuntimeError(f"public handle HEAD observation failed: {exc.reason}") from exc
        finally:
            if response is not None:
                response.close()

        _normalise_public_https_url(str(observation["url_effective"]))
    else:
        observation["observation_status"] = "not_fetched"

    source_item = _source_item_from_public_handle_observation(
        source_item_id=str(source_item_id).strip(),
        source_kind=source_kind,
        freshness=freshness,
        terms_note=effective_terms_note,
        parsed_url=parsed_url,
        observation=observation,
    )
    runtime_policy = build_content_ops_connector_runtime_policy(
        connector_id=f"{surface}_{source_kind}",
        connector_name="public handle browser connector",
        access_mode="public_metadata_only",
        connector_url=normalised_url,
    )
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_PUBLIC_HANDLE_OBSERVATION_PACKET_SCHEMA_VERSION,
        "mode": "content-ops-observe-public-handle",
        "surface": surface,
        "source_item": source_item,
        "source_item_schema_version": SOURCE_ITEM_SCHEMA_VERSION,
        "observation": observation,
        "runtime_policy": runtime_policy,
        "external_reads_performed": bool(fetch),
        "external_read_kind": "http_head" if fetch else "none",
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "private_source_content_read": False,
        "autopublish_allowed": False,
        "promotion_target": "source_item_v0",
        "next_safe_action": (
            "promote the compact source_item_v0 into content_ops_surface_v0 "
            "only after attribution and allowed_use remain metadata_only"
        ),
    }


def build_content_ops_private_connector_gate_packet(
    *,
    connector_id: str = "chatlog_alpha_chatview",
    connector_name: str = "chatlog-alpha/chatview",
    surface: str = "wechat_private_archive",
    proposed_source_item_id: str = "source_wechat_metadata_signal_001",
    source_kind: str = "wechat_private_connector_metadata",
    owner_label: str = "WeChat archive owner",
    freshness: str = "unknown",
) -> dict[str, Any]:
    """Project a concrete owner gate before private connector intake.

    This packet is a routing artifact only. It does not connect to private
    sources, read source content, quote material, or authorize publication.
    """

    if not str(connector_id or "").strip():
        raise ValueError("connector_id is required")
    if not str(proposed_source_item_id or "").strip():
        raise ValueError("proposed_source_item_id is required")
    if freshness not in ALLOWED_FRESHNESS:
        raise ValueError(f"freshness must be one of {sorted(ALLOWED_FRESHNESS)}")

    gate_id = f"owner_gate_{str(connector_id).strip()}"
    runtime_policy = build_content_ops_connector_runtime_policy(
        connector_id=str(connector_id).strip(),
        connector_name=str(connector_name).strip(),
        access_mode="private_metadata_only",
        connector_url="https://chatview.zaynjarvis.com/"
        if str(connector_id).strip() == "chatlog_alpha_chatview"
        else None,
    )
    gate = {
        "schema_version": CONTENT_OPS_PRIVATE_CONNECTOR_OWNER_GATE_SCHEMA_VERSION,
        "gate_id": gate_id,
        "connector_id": str(connector_id).strip(),
        "connector_name": str(connector_name).strip(),
        "surface": surface,
        "status": "blocked_until_user_approval",
        "approval_required": True,
        "owner_label": str(owner_label).strip(),
        "requested_decision": "approve_metadata_only_intake_or_reject",
        "approval_options": [
            "approve metadata-only intake",
            "reject connector intake",
            "request a narrower source handle",
        ],
        "forbidden_until_approved": [
            "source content read",
            "source quote",
            "source summary",
            "external posting",
            "autopublish",
        ],
        "allowed_before_approval": [
            "store this compact gate packet",
            "display the owner question",
            "prepare fixture-only smoke coverage",
        ],
        "runtime_policy": runtime_policy,
    }
    source_item = _source_item_from_private_connector_gate(
        source_item_id=str(proposed_source_item_id).strip(),
        source_kind=source_kind,
        freshness=freshness,
        connector_name=str(connector_name).strip(),
        owner_label=str(owner_label).strip(),
        gate=gate,
    )
    user_todo_projection = {
        "role": "user",
        "action_kind": "content_ops_private_connector_owner_gate",
        "title": (
            f"Approve, reject, or narrow metadata-only intake for {connector_name} "
            "before LoopX reads any private source content."
        ),
        "gate_id": gate_id,
        "connector_id": str(connector_id).strip(),
        "source_item_id": source_item["source_item_id"],
        "validation_surface": "owner decision recorded before private source use",
    }
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_PRIVATE_CONNECTOR_GATE_PACKET_SCHEMA_VERSION,
        "mode": "content-ops-project-private-connector-gate",
        "surface": surface,
        "connector": {
            "connector_id": str(connector_id).strip(),
            "connector_name": str(connector_name).strip(),
            "access_mode": "private_metadata_only",
            "source_status": "private_needs_review",
            "allowed_use": "metadata_only",
            "promotion_target": "source_item_v0_after_owner_gate",
        },
        "owner_gate": gate,
        "runtime_policy": runtime_policy,
        "source_item": source_item,
        "user_todo_projection": user_todo_projection,
        "external_reads_performed": False,
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "private_source_content_read": False,
        "autopublish_allowed": False,
        "owner_gate_required": True,
        "next_safe_action": (
            "surface the projected owner gate; do not read private source content "
            "until an owner decision updates the gate"
        ),
    }


def _non_negative_int(value: int, label: str) -> int:
    count = int(value)
    if count < 0:
        raise ValueError(f"{label} must be non-negative")
    return count


def _normalise_api_path_counts(values: Mapping[str, Any] | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_path, raw_count in (values or {}).items():
        path = str(raw_path or "").strip()
        if not path.startswith("/api/"):
            raise ValueError("ChatView API path counts must use /api/ path classes")
        counts[path] = _non_negative_int(int(raw_count), f"api path count for {path}")
    return dict(sorted(counts.items()))


def _normalise_theme_signals(values: Sequence[str] | None) -> list[str]:
    signals: list[str] = []
    for raw_value in values or []:
        signal = _text(raw_value, limit=80)
        if not signal:
            continue
        lowered = signal.lower()
        if (
            "http://" in lowered
            or "https://" in lowered
            or "/users/" in lowered
            or "/private/" in lowered
            or "bearer " in lowered
            or "credential" in lowered
            or "secret" in lowered
        ):
            raise ValueError("theme_signal must be a compact public-safe label")
        signals.append(signal)
    return signals[:8]


def build_content_ops_chatview_report_packet(
    *,
    channel_count: int,
    recent_record_count: int,
    report_count: int,
    api_request_count: int,
    api_path_counts: Mapping[str, Any] | None = None,
    connector_url: str = "https://chatview.zaynjarvis.com/",
    source_item_id: str = "source_chatview_metadata_signal_001",
    owner_label: str = "ChatView owner",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a public-safe ChatView report that remains aggregation-compatible."""

    channels = _non_negative_int(channel_count, "channel_count")
    recent_records = _non_negative_int(recent_record_count, "recent_record_count")
    reports = _non_negative_int(report_count, "report_count")
    api_requests = _non_negative_int(api_request_count, "api_request_count")
    path_counts = _normalise_api_path_counts(api_path_counts)
    if not str(source_item_id or "").strip():
        raise ValueError("source_item_id is required")
    normalised_url, _parsed = _normalise_public_https_url(connector_url)

    operator_card = (
        f"{channels} channels, {recent_records} recent records, {reports} reports "
        "detected; private source use remains gated."
    )
    packet = build_content_ops_private_connector_gate_packet(
        connector_id="chatlog_alpha_chatview",
        connector_name="chatlog-alpha/chatview",
        surface="wechat_private_archive",
        proposed_source_item_id=str(source_item_id).strip(),
        source_kind="wechat_private_connector_metadata",
        owner_label=owner_label,
        freshness="fresh",
    )
    source_item = packet.get("source_item")
    if isinstance(source_item, dict):
        source_item["summary"] = (
            "ChatView metadata signal: "
            f"{operator_card} No source body or response payload was saved."
        )
        source_item["terms_note"] = (
            "metadata-only ChatView connector report; owner approval is required "
            "before any source content read, quote, summary, or publication"
        )
    user_todo = packet.get("user_todo_projection")
    if isinstance(user_todo, dict):
        user_todo["title"] = (
            "Approve, reject, or narrow ChatView metadata use before LoopX reads "
            "any private source content."
        )
        user_todo["validation_surface"] = (
            "owner decision recorded after reviewing the public-safe operator card"
        )

    report = {
        "schema_version": CONTENT_OPS_CHATVIEW_CONNECTOR_REPORT_SCHEMA_VERSION,
        "generated_at": generated_at if generated_at is not None else now_utc_iso(),
        "connector_url": normalised_url,
        "operator_card": operator_card,
        "observed_shape": {
            "channel_count": channels,
            "recent_record_count": recent_records,
            "report_count": reports,
            "api_request_count": api_requests,
            "api_path_counts": path_counts,
        },
        "aggregation": {
            "aggregation_ready": True,
            "private_gate_packet_schema_version": (
                CONTENT_OPS_PRIVATE_CONNECTOR_GATE_PACKET_SCHEMA_VERSION
            ),
            "source_item_id": str(source_item_id).strip(),
        },
        "boundary": {
            "source_bodies_saved": False,
            "response_payloads_saved": False,
            "external_write_performed": False,
            "autopublish_allowed": False,
        },
    }
    packet.update(
        {
            "mode": "content-ops-project-chatview-report",
            "chatview_report": report,
            "operator_card": operator_card,
            "aggregation_ready": True,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "private_source_bodies_read": False,
            "private_source_content_read": False,
            "autopublish_allowed": False,
            "next_safe_action": (
                "aggregate this private gate packet with public source packets, "
                "or ask the owner to approve a narrower ChatView source handle"
            ),
        }
    )
    return packet


def build_content_ops_surface_from_connector_packets(
    *,
    public_handle_packets: Sequence[Mapping[str, Any]],
    private_connector_gate_packets: Sequence[Mapping[str, Any]],
    surface_id: str = "content_ops_connector_packet_aggregation",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Aggregate connector packets into a compact content-ops state surface."""

    public_packets = [dict(packet) for packet in public_handle_packets]
    private_packets = [dict(packet) for packet in private_connector_gate_packets]
    if not public_packets:
        raise ValueError("at least one public handle observation packet is required")
    if not private_packets:
        raise ValueError("at least one private connector gate packet is required")

    source_items: list[dict[str, Any]] = []
    connector_trials: list[dict[str, Any]] = []

    for index, packet in enumerate(public_packets):
        source_item, connector_trial = source_item_and_trial_from_public_packet(
            packet,
            index,
        )
        source_items.append(source_item)
        connector_trials.append(connector_trial)

    for index, packet in enumerate(private_packets):
        source_item, connector_trial = source_item_and_trial_from_private_gate_packet(
            packet,
            index,
        )
        source_items.append(source_item)
        connector_trials.append(connector_trial)

    public_source_ids = [
        str(item.get("source_item_id"))
        for item in source_items
        if item.get("source_status") == "public"
    ]
    private_source_ids = [
        str(item.get("source_item_id"))
        for item in source_items
        if item.get("source_status") == "private_needs_review"
    ]
    aggregate_angle_id = "angle_connector_packet_aggregation"
    publish_gate_id = "publish_gate_connector_packet_aggregation"
    draft_id = "draft_connector_packet_aggregation_outline"

    angle_candidates = [
        {
            "schema_version": ANGLE_CANDIDATE_SCHEMA_VERSION,
            "angle_id": aggregate_angle_id,
            "source_item_ids": public_source_ids,
            "audience": "creator operators evaluating bounded automation",
            "topic": "connector-bounded content operations",
            "novelty": "combines public metadata packets with explicit private owner gates",
            "preference_fit": "medium",
            "evidence_quality": "metadata_only_connector_packet",
            "decision": "draft",
        }
    ]
    if private_source_ids:
        angle_candidates.append(
            {
                "schema_version": ANGLE_CANDIDATE_SCHEMA_VERSION,
                "angle_id": "angle_private_connector_source_quote",
                "source_item_ids": private_source_ids,
                "audience": "creator operators evaluating bounded automation",
                "topic": "private connector source quote",
            "novelty": "blocked by private-source owner gate",
            "preference_fit": "unknown",
            "evidence_quality": "needs_owner_review",
            "decision": "reject",
            "rejection_reason": (
                "private connector material cannot be quoted or summarized "
                "before owner approval"
            ),
            }
        )

    draft_items = [
        {
            "schema_version": DRAFT_ITEM_SCHEMA_VERSION,
            "draft_id": draft_id,
            "angle_id": aggregate_angle_id,
            "state": "outline",
            "source_map": [
                {"source_item_id": source_id, "use": "metadata-only premise"}
                for source_id in public_source_ids
            ],
            "preference_hints": [
                "explain connector value as bounded signal intake",
                "keep private-source use and publishing behind explicit gates",
            ],
            "publish_gate_id": publish_gate_id,
            "validation_surface": (
                "public source map present; private connector represented only by owner gate"
            ),
        }
    ]
    feedback_signals = [
        {
            "schema_version": FEEDBACK_SIGNAL_SCHEMA_VERSION,
            "feedback_id": "feedback_connector_packet_boundary",
            "target_id": private_source_ids[0],
            "signal": "private_connector_stays_gated",
            "effect": "source_boundary_correction",
            "writes_todo": True,
            "summary": "Private connector packets remain owner-gated before source use.",
        }
    ]
    publish_gates = [
        {
            "schema_version": PUBLISH_GATE_SCHEMA_VERSION,
            "gate_id": publish_gate_id,
            "draft_id": draft_id,
            "status": "blocked_until_user_approval",
            "approval_required": True,
            "autopublish_allowed": False,
            "required_review": [
                "source attribution",
                "private connector owner gate",
                "tone/style",
                "final publish destination",
            ],
        }
    ]
    material_memory = [
        {
            "schema_version": MATERIAL_MEMORY_SCHEMA_VERSION,
            "memory_id": f"memory_{source_id}",
            "source_item_id": source_id,
            "attribution": "content-ops packet aggregation",
            "reuse_boundary": "metadata_only_public_handle",
            "rejected_angles": ["angle_private_connector_source_quote"],
            "preference_hints": ["bounded connector intake before drafting"],
        }
        for source_id in public_source_ids
    ]
    material_memory.extend(
        {
            "schema_version": MATERIAL_MEMORY_SCHEMA_VERSION,
            "memory_id": f"memory_{source_id}",
            "source_item_id": source_id,
            "attribution": "content-ops private connector gate",
            "reuse_boundary": "private_owner_gate_required",
            "rejected_angles": ["angle_private_connector_source_quote"],
            "preference_hints": ["do not quote or summarize before owner approval"],
        }
        for source_id in private_source_ids
    )

    return {
        "schema_version": CONTENT_OPS_SURFACE_SCHEMA_VERSION,
        "surface_id": surface_id,
        "generated_at": generated_at if generated_at is not None else now_utc_iso(),
        "mode": "compact_state_surface",
        "source_items": source_items,
        "angle_candidates": angle_candidates,
        "draft_items": draft_items,
        "feedback_signals": feedback_signals,
        "publish_gates": publish_gates,
        "material_memory": material_memory,
        "connector_trials": connector_trials,
        "operator_states": [
            "waiting_for_source_review",
            "ready_to_draft",
            "waiting_for_feedback",
            "ready_for_publish_decision",
            "safe_side_work_available",
        ],
        "boundary": {
            "public_safe": True,
            "raw_private_material_recorded": False,
            "raw_platform_data_recorded": False,
            "credentials_recorded": False,
            "autopublish_allowed": False,
            "publish_requires_user_gate": True,
            "connector_bodies_are_source_of_truth": False,
        },
    }


def build_content_ops_packet_aggregation_packet(
    *,
    public_handle_packets: Sequence[Mapping[str, Any]],
    private_connector_gate_packets: Sequence[Mapping[str, Any]],
    surface_id: str = "content_ops_connector_packet_aggregation",
    generated_at: str | None = None,
) -> dict[str, Any]:
    surface = build_content_ops_surface_from_connector_packets(
        public_handle_packets=public_handle_packets,
        private_connector_gate_packets=private_connector_gate_packets,
        surface_id=surface_id,
        generated_at=generated_at,
    )
    validation = validate_content_ops_surface(surface)
    projection = project_content_ops_surface(surface)
    return {
        "ok": bool(validation.get("ok")),
        "schema_version": CONTENT_OPS_PACKET_AGGREGATION_SCHEMA_VERSION,
        "mode": "content-ops-aggregate-packets",
        "surface": surface,
        "projection": projection,
        "validation": validation,
        "input_summary": {
            "public_handle_packet_count": len(public_handle_packets),
            "private_connector_gate_packet_count": len(private_connector_gate_packets),
            "source_item_count": len(surface.get("source_items") or []),
            "owner_gate_required_count": projection.get("connector_trials", {}).get(
                "owner_gate_required_count"
            )
            if isinstance(projection.get("connector_trials"), Mapping)
            else None,
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "private_source_content_read": False,
        "autopublish_allowed": False,
        "next_safe_action": projection.get("first_screen", {}).get("next_safe_action")
        if isinstance(projection.get("first_screen"), Mapping)
        else None,
    }


def build_content_ops_walkthrough_artifact_packet(
    *,
    public_handle_url: str,
    public_source_item_id: str = "source_x_public_handle_walkthrough",
    public_surface: str = "x_public_feed",
    public_source_kind: str = "x_public_profile_handle",
    chatview_source_item_id: str = "source_chatview_metadata_signal_walkthrough",
    channel_count: int,
    recent_record_count: int,
    report_count: int,
    api_request_count: int,
    api_path_counts: Mapping[str, Any] | None = None,
    private_preview_item_count: int = 0,
    theme_signals: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the public-safe operator artifact for the content-ops chain."""

    if generated_at is None:
        generated_at = now_utc_iso()
    preview_count = _non_negative_int(
        private_preview_item_count,
        "private_preview_item_count",
    )
    themes = _normalise_theme_signals(theme_signals)
    public_packet = build_content_ops_public_handle_observation_packet(
        url=public_handle_url,
        source_item_id=public_source_item_id,
        surface=public_surface,
        source_kind=public_source_kind,
        freshness="fresh",
        terms_note="metadata-only public handle signal for walkthrough artifact",
        fetch=False,
    )
    chatview_packet = build_content_ops_chatview_report_packet(
        channel_count=channel_count,
        recent_record_count=recent_record_count,
        report_count=report_count,
        api_request_count=api_request_count,
        api_path_counts=api_path_counts,
        source_item_id=chatview_source_item_id,
        generated_at=generated_at,
    )
    aggregate = build_content_ops_packet_aggregation_packet(
        public_handle_packets=[public_packet],
        private_connector_gate_packets=[chatview_packet],
        surface_id="content_ops_walkthrough_artifact_surface",
        generated_at=generated_at,
    )

    surface = aggregate.get("surface") if isinstance(aggregate.get("surface"), Mapping) else {}
    projection = (
        aggregate.get("projection")
        if isinstance(aggregate.get("projection"), Mapping)
        else {}
    )
    first_screen = (
        projection.get("first_screen")
        if isinstance(projection.get("first_screen"), Mapping)
        else {}
    )
    source_items = _as_mappings(surface.get("source_items"))  # type: ignore[arg-type]
    draft_items = _as_mappings(surface.get("draft_items"))  # type: ignore[arg-type]
    publish_gates = _as_mappings(surface.get("publish_gates"))  # type: ignore[arg-type]
    chatview_report = (
        chatview_packet.get("chatview_report")
        if isinstance(chatview_packet.get("chatview_report"), Mapping)
        else {}
    )
    chatview_observed = (
        chatview_report.get("observed_shape")
        if isinstance(chatview_report.get("observed_shape"), Mapping)
        else {}
    )
    gate = publish_gates[0] if publish_gates else {}
    draft = draft_items[0] if draft_items else {}

    operator_artifact = {
        "headline": (
            "Public and private connector signals can reach a draft plan, but "
            "private source use and publication stay behind explicit gates."
        ),
        "source_cards": [
            {
                "source_item_id": item.get("source_item_id"),
                "source_status": item.get("source_status"),
                "allowed_use": item.get("allowed_use"),
                "summary": item.get("summary"),
            }
            for item in source_items
        ],
        "private_operator_preview": {
            "available_in_current_operator_session": preview_count > 0,
            "sample_record_count": preview_count,
            "theme_signals": themes,
            "stored_in_repo": False,
            "source_content_recorded": False,
            "response_payload_recorded": False,
        },
        "draft_gate": {
            "draft_id": draft.get("draft_id"),
            "state": draft.get("state"),
            "publish_gate_id": gate.get("gate_id"),
            "publish_status": gate.get("status"),
            "approval_required": gate.get("approval_required"),
            "autopublish_allowed": gate.get("autopublish_allowed"),
        },
        "next_actions": [
            "review the private connector owner gate before source use",
            "draft only from source-mapped metadata until approval changes",
            "ask for final publish approval before any external posting",
        ],
    }

    chain_steps = [
        {
            "step": "public_signal_intake",
            "result": "metadata-only public handle packet",
            "source_item_id": public_source_item_id,
            "external_write_performed": False,
        },
        {
            "step": "private_connector_operator_card",
            "result": chatview_report.get("operator_card"),
            "observed_shape": chatview_observed,
            "owner_gate_required": True,
        },
        {
            "step": "aggregate_surface_projection",
            "result": first_screen.get("next_safe_action"),
            "waiting_on": first_screen.get("waiting_on"),
            "user_action_required": first_screen.get("user_action_required"),
        },
        {
            "step": "draft_publish_gate",
            "result": gate.get("status"),
            "approval_required": gate.get("approval_required"),
            "autopublish_allowed": gate.get("autopublish_allowed"),
        },
    ]

    return {
        "ok": bool(aggregate.get("ok")),
        "schema_version": CONTENT_OPS_WALKTHROUGH_ARTIFACT_SCHEMA_VERSION,
        "mode": "content-ops-walkthrough-artifact",
        "generated_at": generated_at,
        "operator_artifact": operator_artifact,
        "chain_steps": chain_steps,
        "aggregation_projection": projection,
        "validation": aggregate.get("validation"),
        "packet_summary": {
            "public_packet_schema_version": public_packet.get("schema_version"),
            "chatview_packet_schema_version": chatview_packet.get("schema_version"),
            "aggregation_schema_version": aggregate.get("schema_version"),
            "source_item_count": aggregate.get("input_summary", {}).get(
                "source_item_count"
            )
            if isinstance(aggregate.get("input_summary"), Mapping)
            else None,
            "owner_gate_required_count": aggregate.get("input_summary", {}).get(
                "owner_gate_required_count"
            )
            if isinstance(aggregate.get("input_summary"), Mapping)
            else None,
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "private_source_content_read": False,
        "autopublish_allowed": False,
        "public_repo_safe": True,
        "next_safe_action": (
            "show the operator artifact, then collect owner approval before "
            "private source use or publication"
        ),
    }
