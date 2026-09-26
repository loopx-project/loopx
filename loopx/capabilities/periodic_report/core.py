from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from ...control_plane.runtime.public_safety import SECRET_LIKE_SURFACE_PATTERN


REQUEST_SCHEMA = "periodic_report_run_request_v0"
RUN_SCHEMA = "periodic_report_v0"
TRIGGER_DECISION_SCHEMA = "periodic_report_trigger_decision_v0"

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SOURCE_STATUSES = {"complete", "partial", "failed", "unknown"}
_ARTIFACT_STATUSES = {"pending", "rendered", "failed", "unknown"}
_SINK_STATUSES = {"pending", "sent", "failed", "skipped", "unknown"}
_SINK_ROLES = {"archive", "delivery"}
_REPORT_KINDS = {
    "cadence_digest",
    "exception_update",
    "manual_update",
    "milestone_update",
}
_REPORTABLE_TRIGGER_KINDS = {
    "bounded_segment_milestone",
    "cadence_due",
    "manual",
    "material_blocker",
    "material_decision",
    "material_recovery",
    "primary_goal_outcome",
    "vision_closed",
}
_LOCAL_PATH_SURFACE_PATTERN = re.compile(
    r"(?<!<)/(?:Users|Volumes|home|var/folders|tmp|private/tmp)/[^\s`'\"<>]+"
)
_FORBIDDEN_RAW_KEYS = {
    "credential",
    "credentials",
    "private_path",
    "raw_body",
    "raw_content",
    "raw_log",
    "raw_logs",
    "raw_message",
    "raw_payload",
    "raw_transcript",
    "secret",
    "token",
}


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _optional_text(
    value: object,
    label: str,
    *,
    maximum: int = 1000,
) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _token(value: object, label: str) -> str:
    token = _text(value, label, maximum=128).lower()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"{label} must be a lower-snake-like public token")
    return token


def _boolean(value: object, label: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 1000000,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return number


def _reject_raw_keys(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_RAW_KEYS:
                raise ValueError(
                    f"{label} contains forbidden raw/private field {raw_key!r}"
                )
            _reject_raw_keys(item, f"{label}.{raw_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_raw_keys(item, f"{label}[{index}]")
    elif isinstance(value, str) and (
        _LOCAL_PATH_SURFACE_PATTERN.search(value)
        or SECRET_LIKE_SURFACE_PATTERN.search(value)
    ):
        raise ValueError(f"{label} contains a private path or credential-like value")


def _digest(value: object, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _timestamp(value: object, label: str) -> str:
    raw = _text(value, label, maximum=80)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_window(raw: object) -> dict[str, str]:
    window = _object(raw, "period_window")
    start_at = _timestamp(window.get("start_at"), "period_window.start_at")
    end_at = _timestamp(window.get("end_at"), "period_window.end_at")
    start_value = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    end_value = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    if start_value >= end_value:
        raise ValueError("period_window.start_at must be earlier than end_at")
    return {"start_at": start_at, "end_at": end_at}


def _validate_report_window_for_trigger(
    *, generated_at: str, period_window: Mapping[str, str], trigger_receipt: Mapping[str, Any] | None
) -> None:
    """Keep cadence digests retrospective while leaving event updates incremental.

    A calendar boundary is an eligibility signal, not permission to report an
    unfinished interval.  Event-driven updates may still use the same bounded
    run envelope, but their trigger receipt—not the calendar—defines why the
    update is being published.
    """

    if not trigger_receipt or trigger_receipt.get("report_kind") != "cadence_digest":
        return
    generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(
        str(period_window["end_at"]).replace("Z", "+00:00")
    )
    if end_at > generated:
        raise ValueError(
            "cadence_digest period_window.end_at must not be later than generated_at"
        )


def _normalize_profile(raw: object) -> dict[str, str]:
    profile = _object(raw, "profile")
    normalized = {
        "profile_id": _token(profile.get("profile_id"), "profile.profile_id"),
        "profile_version": _token(
            profile.get("profile_version"), "profile.profile_version"
        ),
    }
    profile_ref = _optional_text(
        profile.get("profile_ref"), "profile.profile_ref", maximum=500
    )
    if profile_ref:
        normalized["profile_ref"] = profile_ref
    return normalized


def _normalize_sources(raw: object) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(_list(raw, "source_snapshots")):
        label = f"source_snapshots[{index}]"
        item = _object(value, label)
        source_id = _token(item.get("source_id"), f"{label}.source_id")
        if source_id in seen:
            raise ValueError(f"duplicate source_id {source_id!r}")
        seen.add(source_id)
        status = _token(item.get("status"), f"{label}.status")
        if status not in _SOURCE_STATUSES:
            raise ValueError(f"{label}.status is invalid")
        snapshot: dict[str, Any] = {
            "source_id": source_id,
            "source_kind": _token(item.get("source_kind"), f"{label}.source_kind"),
            "status": status,
            "retryable": _boolean(item.get("retryable"), f"{label}.retryable"),
        }
        observed_at = item.get("observed_at")
        snapshot_digest = _optional_text(
            item.get("snapshot_digest"), f"{label}.snapshot_digest", maximum=256
        )
        snapshot_ref = _optional_text(
            item.get("snapshot_ref"), f"{label}.snapshot_ref", maximum=500
        )
        if status in {"complete", "partial"}:
            if not snapshot_digest:
                raise ValueError(
                    f"{label}.snapshot_digest is required for {status} sources"
                )
            if observed_at is None:
                raise ValueError(
                    f"{label}.observed_at is required for {status} sources"
                )
        if observed_at is not None:
            snapshot["observed_at"] = _timestamp(observed_at, f"{label}.observed_at")
        if snapshot_digest:
            snapshot["snapshot_digest"] = snapshot_digest
        if snapshot_ref:
            snapshot["snapshot_ref"] = snapshot_ref
        if "item_count" in item:
            snapshot["item_count"] = _integer(
                item.get("item_count"), f"{label}.item_count"
            )
        snapshots.append(snapshot)
    if not snapshots:
        raise ValueError("source_snapshots must not be empty")
    return sorted(snapshots, key=lambda item: str(item["source_id"]))


def _normalize_retry_policy(raw: object) -> dict[str, int]:
    policy = _object(raw, "retry_policy")
    attempt = _integer(
        policy.get("attempt", 1), "retry_policy.attempt", minimum=1, maximum=100
    )
    max_attempts = _integer(
        policy.get("max_attempts", 3),
        "retry_policy.max_attempts",
        minimum=1,
        maximum=100,
    )
    if attempt > max_attempts:
        raise ValueError("retry_policy.attempt must not exceed max_attempts")
    return {"attempt": attempt, "max_attempts": max_attempts}


def _normalize_trigger_receipt(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    receipt = _object(raw, "trigger_receipt")
    schema_version = _text(
        receipt.get("schema_version"), "trigger_receipt.schema_version"
    )
    if schema_version != TRIGGER_DECISION_SCHEMA:
        raise ValueError(
            f"trigger_receipt.schema_version must be {TRIGGER_DECISION_SCHEMA!r}"
        )
    if not _boolean(receipt.get("eligible"), "trigger_receipt.eligible"):
        raise ValueError("trigger_receipt must contain an eligible decision")
    report_kind = _token(receipt.get("report_kind"), "trigger_receipt.report_kind")
    if report_kind not in _REPORT_KINDS:
        raise ValueError("trigger_receipt.report_kind is invalid")
    selected_trigger_id = _token(
        receipt.get("selected_trigger_id"),
        "trigger_receipt.selected_trigger_id",
    )
    trigger_ids: list[str] = []
    for index, value in enumerate(
        _list(
            receipt.get("coalesced_trigger_ids"),
            "trigger_receipt.coalesced_trigger_ids",
        )
    ):
        trigger_id = _token(value, f"trigger_receipt.coalesced_trigger_ids[{index}]")
        if trigger_id not in trigger_ids:
            trigger_ids.append(trigger_id)
    if selected_trigger_id not in trigger_ids:
        raise ValueError(
            "trigger_receipt.selected_trigger_id must be coalesced into the report"
        )
    profile = _normalize_profile(receipt.get("profile"))
    raw_policy = _object(receipt.get("trigger_policy"), "trigger_receipt.trigger_policy")
    enabled_kinds: list[str] = []
    for index, value in enumerate(
        _list(
            raw_policy.get("enabled_kinds"),
            "trigger_receipt.trigger_policy.enabled_kinds",
        )
    ):
        kind = _token(
            value,
            f"trigger_receipt.trigger_policy.enabled_kinds[{index}]",
        )
        if kind not in _REPORTABLE_TRIGGER_KINDS:
            raise ValueError(
                "trigger_receipt.trigger_policy.enabled_kinds contains an invalid kind"
            )
        if kind not in enabled_kinds:
            enabled_kinds.append(kind)
    if not enabled_kinds:
        raise ValueError(
            "trigger_receipt.trigger_policy.enabled_kinds must not be empty"
        )
    trigger_policy = {
        "enabled_kinds": sorted(enabled_kinds),
        "minimum_interval_seconds": _integer(
            raw_policy.get("minimum_interval_seconds", 0),
            "trigger_receipt.trigger_policy.minimum_interval_seconds",
            maximum=31 * 24 * 60 * 60,
        ),
    }
    if raw_policy.get("aggregation") is not None:
        raw_aggregation = _object(
            raw_policy.get("aggregation"),
            "trigger_receipt.trigger_policy.aggregation",
        )
        aggregation: dict[str, Any] = {
            "window_seconds": _integer(
                raw_aggregation.get("window_seconds"),
                "trigger_receipt.trigger_policy.aggregation.window_seconds",
                minimum=1,
                maximum=31 * 24 * 60 * 60,
            ),
            "promote_replan": _boolean(
                raw_aggregation.get("promote_replan"),
                "trigger_receipt.trigger_policy.aggregation.promote_replan",
            ),
            "stage_completion_required": _boolean(
                raw_aggregation.get("stage_completion_required", True),
                "trigger_receipt.trigger_policy.aggregation."
                "stage_completion_required",
            ),
        }
        if raw_aggregation.get("todo_completed_threshold") is not None:
            aggregation["todo_completed_threshold"] = _integer(
                raw_aggregation.get("todo_completed_threshold"),
                "trigger_receipt.trigger_policy.aggregation.todo_completed_threshold",
                minimum=1,
                maximum=64,
            )
        if (
            "todo_completed_threshold" not in aggregation
            and not aggregation["promote_replan"]
            and not aggregation["stage_completion_required"]
        ):
            raise ValueError(
                "trigger_receipt.trigger_policy.aggregation must enable "
                "a stage completion, todo threshold, or replan promotion"
            )
        trigger_policy["aggregation"] = aggregation
    expected_report_key = _digest(
        {
            "profile": profile,
            "trigger_policy": trigger_policy,
            "report_kind": report_kind,
            "trigger_ids": sorted(trigger_ids),
        },
        prefix="report",
    )
    report_key = _token(receipt.get("report_key"), "trigger_receipt.report_key")
    if report_key != expected_report_key:
        raise ValueError("trigger_receipt.report_key does not match trigger identity")
    return {
        "schema_version": schema_version,
        "eligible": True,
        "profile": profile,
        "trigger_policy": trigger_policy,
        "decision_id": _token(
            receipt.get("decision_id"), "trigger_receipt.decision_id"
        ),
        "report_key": report_key,
        "report_kind": report_kind,
        "selected_trigger_id": selected_trigger_id,
        "coalesced_trigger_ids": sorted(trigger_ids),
    }


def _normalize_artifact(
    raw: object,
    *,
    attempt: int,
) -> dict[str, Any]:
    item = _object(raw, "artifact_receipt")
    status = _token(item.get("status"), "artifact_receipt.status")
    if status not in _ARTIFACT_STATUSES:
        raise ValueError("artifact_receipt.status is invalid")
    receipt_attempt = _integer(
        item.get("attempt", attempt),
        "artifact_receipt.attempt",
        minimum=1,
        maximum=attempt,
    )
    receipt: dict[str, Any] = {
        "artifact_id": _token(item.get("artifact_id"), "artifact_receipt.artifact_id"),
        "renderer_id": _token(item.get("renderer_id"), "artifact_receipt.renderer_id"),
        "renderer_kind": _token(
            item.get("renderer_kind"), "artifact_receipt.renderer_kind"
        ),
        "status": status,
        "attempt": receipt_attempt,
        "retryable": _boolean(item.get("retryable"), "artifact_receipt.retryable"),
    }
    artifact_digest = _optional_text(
        item.get("artifact_digest"),
        "artifact_receipt.artifact_digest",
        maximum=256,
    )
    artifact_ref = _optional_text(
        item.get("artifact_ref"), "artifact_receipt.artifact_ref", maximum=500
    )
    if status == "rendered" and (not artifact_digest or not artifact_ref):
        raise ValueError(
            "rendered artifact_receipt requires artifact_digest and artifact_ref"
        )
    if artifact_digest:
        receipt["artifact_digest"] = artifact_digest
    if artifact_ref:
        receipt["artifact_ref"] = artifact_ref
    return receipt


def _run_identity(
    *,
    period_window: Mapping[str, str],
    profile: Mapping[str, str],
    sources: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    sinks: Sequence[Mapping[str, Any]],
    trigger_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    identity = {
        "period_window": dict(period_window),
        "profile": dict(profile),
        "sources": [
            {
                "source_id": source["source_id"],
                "source_kind": source["source_kind"],
            }
            for source in sources
        ],
        "renderer": {
            "renderer_id": artifact["renderer_id"],
            "renderer_kind": artifact["renderer_kind"],
        },
        "sinks": [
            {
                "sink_id": sink["sink_id"],
                "sink_kind": sink["sink_kind"],
                "sink_role": sink["sink_role"],
            }
            for sink in sinks
        ],
    }
    if trigger_receipt:
        identity["trigger"] = {
            "report_key": trigger_receipt["report_key"],
            "report_kind": trigger_receipt["report_kind"],
        }
    return identity


def _sink_idempotency_key(run_id: str, sink_role: str, sink_id: str) -> str:
    return _digest(
        {"run_id": run_id, "sink_role": sink_role, "sink_id": sink_id},
        prefix="delivery",
    )


def _normalize_sink_identities(raw: object) -> list[dict[str, Any]]:
    identities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(_list(raw, "sink_receipts")):
        label = f"sink_receipts[{index}]"
        item = _object(value, label)
        sink_id = _token(item.get("sink_id"), f"{label}.sink_id")
        sink_role = _token(item.get("sink_role"), f"{label}.sink_role")
        if sink_role not in _SINK_ROLES:
            raise ValueError(f"{label}.sink_role is invalid")
        identity = (sink_role, sink_id)
        if identity in seen:
            raise ValueError(f"duplicate sink identity {sink_role}:{sink_id}")
        seen.add(identity)
        identities.append(
            {
                "sink_id": sink_id,
                "sink_kind": _token(item.get("sink_kind"), f"{label}.sink_kind"),
                "sink_role": sink_role,
                "raw": item,
                "label": label,
            }
        )
    roles = {str(item["sink_role"]) for item in identities}
    if roles != _SINK_ROLES:
        raise ValueError(
            "sink_receipts requires at least one archive and delivery sink"
        )
    return sorted(
        identities,
        key=lambda item: (str(item["sink_role"]), str(item["sink_id"])),
    )


def _normalize_sinks(
    identities: list[dict[str, Any]],
    *,
    run_id: str,
    attempt: int,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for identity in identities:
        item = identity["raw"]
        label = str(identity["label"])
        status = _token(item.get("status"), f"{label}.status")
        if status not in _SINK_STATUSES:
            raise ValueError(f"{label}.status is invalid")
        expected_key = _sink_idempotency_key(
            run_id,
            str(identity["sink_role"]),
            str(identity["sink_id"]),
        )
        supplied_key = _optional_text(
            item.get("idempotency_key"), f"{label}.idempotency_key", maximum=128
        )
        if supplied_key and supplied_key != expected_key:
            raise ValueError(f"{label}.idempotency_key does not match run identity")
        readback_verified = _boolean(
            item.get("readback_verified"), f"{label}.readback_verified"
        )
        receipt_ref = _optional_text(
            item.get("receipt_ref"), f"{label}.receipt_ref", maximum=500
        )
        if status == "sent" and not supplied_key:
            raise ValueError(f"{label} sent receipt requires idempotency_key")
        if status == "sent" and (not receipt_ref or not readback_verified):
            raise ValueError(
                f"{label} sent receipt requires receipt_ref and verified readback"
            )
        receipt: dict[str, Any] = {
            "sink_id": identity["sink_id"],
            "sink_kind": identity["sink_kind"],
            "sink_role": identity["sink_role"],
            "status": status,
            "attempt": _integer(
                item.get("attempt", attempt),
                f"{label}.attempt",
                minimum=1,
                maximum=attempt,
            ),
            "retryable": _boolean(item.get("retryable"), f"{label}.retryable"),
            "idempotency_key": expected_key,
            "readback_verified": readback_verified,
        }
        if receipt_ref:
            receipt["receipt_ref"] = receipt_ref
        receipts.append(receipt)
    return receipts


def _derive_run_state(
    sources: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    sinks: Sequence[Mapping[str, Any]],
) -> str:
    source_statuses = {str(item["status"]) for item in sources}
    sink_statuses = {str(item["status"]) for item in sinks}
    artifact_status = str(artifact["status"])
    if (
        "unknown" in source_statuses
        or artifact_status == "unknown"
        or "unknown" in sink_statuses
    ):
        return "unknown"
    if "failed" in source_statuses or artifact_status == "failed":
        return "failed"
    if artifact_status == "pending" or "pending" in sink_statuses:
        return "pending"
    if "failed" in sink_statuses:
        return "partial" if "sent" in sink_statuses else "failed"
    if "partial" in source_statuses or "skipped" in sink_statuses:
        return "partial"
    return "succeeded"


def _retry_projection(
    *,
    state: str,
    policy: Mapping[str, int],
    sources: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    sinks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    retryable_components = [
        f"source:{item['source_id']}"
        for item in sources
        if item["status"] != "complete" and item["retryable"]
    ]
    if artifact["status"] not in {"rendered", "pending"} and artifact["retryable"]:
        retryable_components.append(f"artifact:{artifact['artifact_id']}")
    retryable_components.extend(
        f"{item['sink_role']}:{item['sink_id']}"
        for item in sinks
        if item["status"] not in {"sent", "pending"} and item["retryable"]
    )
    attempt = int(policy["attempt"])
    max_attempts = int(policy["max_attempts"])
    allowed = (
        state in {"partial", "failed", "unknown"}
        and attempt < max_attempts
        and bool(retryable_components)
    )
    if state == "succeeded":
        reason = "run_succeeded"
    elif state == "pending":
        reason = "run_not_terminal"
    elif attempt >= max_attempts:
        reason = "attempt_limit_reached"
    elif not retryable_components:
        reason = "no_retryable_component"
    else:
        reason = "retryable_component_failure"
    return {
        "allowed": allowed,
        "reason": reason,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "next_attempt": attempt + 1 if allowed else None,
        "retryable_components": retryable_components,
    }


def build_periodic_report_run(request: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one periodic report attempt without reading or writing providers."""

    payload = _object(request, "request")
    _reject_raw_keys(payload, "request")
    schema_version = _text(payload.get("schema_version"), "schema_version")
    if schema_version != REQUEST_SCHEMA:
        raise ValueError(f"schema_version must be {REQUEST_SCHEMA!r}")

    generated_at = _timestamp(payload.get("generated_at"), "generated_at")
    period_window = _normalize_window(payload.get("period_window"))
    profile = _normalize_profile(payload.get("profile"))
    trigger_receipt = _normalize_trigger_receipt(payload.get("trigger_receipt"))
    if trigger_receipt is not None and trigger_receipt["profile"] != profile:
        raise ValueError("trigger_receipt.profile must match the run profile")
    _validate_report_window_for_trigger(
        generated_at=generated_at,
        period_window=period_window,
        trigger_receipt=trigger_receipt,
    )
    sources = _normalize_sources(payload.get("source_snapshots"))
    retry_policy = _normalize_retry_policy(payload.get("retry_policy", {}))
    artifact = _normalize_artifact(
        payload.get("artifact_receipt"), attempt=retry_policy["attempt"]
    )
    sink_identities = _normalize_sink_identities(payload.get("sink_receipts"))
    identity = _run_identity(
        period_window=period_window,
        profile=profile,
        sources=sources,
        artifact=artifact,
        sinks=sink_identities,
        trigger_receipt=trigger_receipt,
    )
    run_id = _digest(identity, prefix="periodic_report")
    idempotency_key = _digest(identity, prefix="run")
    supplied_run_id = _optional_text(payload.get("run_id"), "run_id", maximum=128)
    supplied_idempotency_key = _optional_text(
        payload.get("idempotency_key"), "idempotency_key", maximum=128
    )
    if supplied_run_id and supplied_run_id != run_id:
        raise ValueError("run_id does not match normalized run identity")
    if supplied_idempotency_key and supplied_idempotency_key != idempotency_key:
        raise ValueError("idempotency_key does not match normalized run identity")

    sinks = _normalize_sinks(
        sink_identities,
        run_id=run_id,
        attempt=retry_policy["attempt"],
    )
    state = _derive_run_state(sources, artifact, sinks)
    retry = _retry_projection(
        state=state,
        policy=retry_policy,
        sources=sources,
        artifact=artifact,
        sinks=sinks,
    )
    result = {
        "ok": True,
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "generated_at": generated_at,
        "period_window": period_window,
        "profile": profile,
        "source_snapshots": sources,
        "artifact_receipt": artifact,
        "sink_receipts": sinks,
        "run_state": {
            "status": state,
            "terminal": state != "pending",
            "partial": state == "partial",
            "unknown": state == "unknown",
        },
        "retry": retry,
        "boundary": {
            "provider_neutral": True,
            "schedule_policy_owned_by_profile": True,
            "source_collection_executes_outside_core": True,
            "rendering_executes_outside_core": True,
            "sink_delivery_executes_outside_core": True,
            "external_writes_performed": False,
            "raw_content_persisted": False,
        },
    }
    if trigger_receipt:
        result["trigger_receipt"] = trigger_receipt
    return result
