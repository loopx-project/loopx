from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, time, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...control_plane.runtime.public_safety import public_safe_compact_text
from .reward_memory import (
    reviewer_artifact_notification_gate,
    reviewer_notification_before_send_gate,
)
from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN


ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_INPUT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_sinks_input_v0"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_RESULT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_sinks_result_v0"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_SINK_RESULT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_sink_result_v0"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_queue_receipt_v1"
)
ISSUE_FIX_REVIEWER_NOTIFICATION_LEGACY_QUEUE_RECEIPT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_queue_receipt_v0"
)
SAFE_LOCAL_KEY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
LOCAL_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

CommandRunner = Callable[[Sequence[str]], Mapping[str, Any]]
NotificationSinkAdapter = Callable[..., dict[str, Any]]


def goal_reviewer_notification_config_path(
    *,
    goal: Mapping[str, Any],
    project: str | Path,
) -> Path | None:
    """Resolve a registered local-private sink config without exposing its path."""

    control_plane = (
        goal.get("control_plane")
        if isinstance(goal.get("control_plane"), Mapping)
        else {}
    )
    issue_fix = (
        control_plane.get("issue_fix")
        if isinstance(control_plane.get("issue_fix"), Mapping)
        else {}
    )
    policy = (
        issue_fix.get("reviewer_notification")
        if isinstance(issue_fix.get("reviewer_notification"), Mapping)
        else {}
    )
    if policy.get("enabled") is not True:
        return None
    raw_path = str(policy.get("config_path") or "").strip().replace("\\", "/")
    relative = PurePosixPath(raw_path)
    if (
        not raw_path
        or relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) < 3
        or relative.parts[:2] != (".loopx", "config")
        or relative.suffix != ".json"
    ):
        raise ValueError("goal reviewer notification config pointer is invalid")
    root = Path(project).expanduser().resolve()
    resolved = (root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "goal reviewer notification config must stay inside the project"
        ) from exc
    return resolved


def load_goal_reviewer_notification_sinks_input(
    *,
    goal: Mapping[str, Any],
    project: str | Path,
) -> dict[str, Any] | None:
    """Load a registered provider sink config without exposing its local path."""

    path = goal_reviewer_notification_config_path(goal=goal, project=project)
    if path is None:
        return None
    if not path.is_file():
        raise ValueError("goal reviewer notification config is registered but missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("goal reviewer notification config is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_INPUT_SCHEMA_VERSION
    ):
        raise ValueError("goal reviewer notification config schema is invalid")
    sinks = payload.get("sinks")
    if not isinstance(sinks, list) or not sinks:
        raise ValueError("goal reviewer notification config must define sinks")
    for sink in sinks:
        if not isinstance(sink, Mapping):
            raise ValueError("goal reviewer notification sinks must be objects")
    return payload


def reviewer_notification_receipts_from_state(
    packet: Mapping[str, Any] | None,
) -> list[str]:
    values = packet.get("reviewer_notification_receipts") if packet else []
    return list(
        dict.fromkeys(
            str(value)
            for value in (values if isinstance(values, list) else [])
            if ENVELOPED_SHA256_PATTERN.fullmatch(str(value))
        )
    )


def reviewer_notification_queue_from_state(
    packet: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    values = packet.get("reviewer_notification_queue") if packet else []
    queue: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, Mapping):
            continue
        key = str(value.get("idempotency_key") or "")
        if (
            value.get("schema_version")
            != ISSUE_FIX_REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION
            or not ENVELOPED_SHA256_PATTERN.fullmatch(key)
            or key in seen
        ):
            continue
        queue.append(dict(value))
        seen.add(key)
    return queue


def reviewer_notification_legacy_queue_from_state(
    packet: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Detect pre-cutover rows without interpreting them as deliverable work."""

    values = packet.get("reviewer_notification_queue") if packet else []
    queue: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, Mapping):
            continue
        key = str(value.get("idempotency_key") or "")
        if (
            value.get("schema_version")
            != ISSUE_FIX_REVIEWER_NOTIFICATION_LEGACY_QUEUE_RECEIPT_SCHEMA_VERSION
            or not ENVELOPED_SHA256_PATTERN.fullmatch(key)
            or key in seen
        ):
            continue
        queue.append(dict(value))
        seen.add(key)
    return queue


def with_reviewer_notification_state(
    sinks_input: Mapping[str, Any],
    receipts: Sequence[str],
    queued_receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    merged_receipts = reviewer_notification_receipts_from_state(
        {"reviewer_notification_receipts": list(receipts)}
    )
    for value in sinks_input.get("receipts") or []:
        text = str(value)
        if ENVELOPED_SHA256_PATTERN.fullmatch(text) and text not in merged_receipts:
            merged_receipts.append(text)

    queue = reviewer_notification_queue_from_state(
        {"reviewer_notification_queue": list(queued_receipts)}
    )
    for value in reviewer_notification_queue_from_state(
        {"reviewer_notification_queue": sinks_input.get("queued_receipts")}
    ):
        if not any(
            item["idempotency_key"] == value["idempotency_key"] for item in queue
        ):
            queue.append(value)
    verified = set(merged_receipts)
    queue = [item for item in queue if item["idempotency_key"] not in verified]
    return {
        **dict(sinks_input),
        "receipts": merged_receipts,
        "queued_receipts": queue,
    }


def with_reviewer_notification_receipts(
    sinks_input: Mapping[str, Any],
    receipts: Sequence[str],
) -> dict[str, Any]:
    return with_reviewer_notification_state(sinks_input, receipts, ())


def _default_runner(args: Sequence[str]) -> Mapping[str, Any]:
    result = subprocess.run(
        list(args),
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _normalise_handle(value: Any) -> str | None:
    text = public_safe_compact_text(value, limit=100).strip().lstrip("@").lower()
    return f"@{text}" if text else None


def _humanize_pr_title(value: Any) -> str:
    title = public_safe_compact_text(value, limit=180) or ""
    title = re.sub(
        r"^(?:fix|bugfix)(?:\([^)]*\))?!?\s*:\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return title.rstrip(".。")


def _normalise_issue_refs(values: Sequence[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if re.fullmatch(r"#\d+", str(value).strip())
        )
    )[:3]


def _idempotency_key(
    *,
    repo: str,
    pr_number: int,
    sink_kind: str,
    sink_instance_key: str,
    reviewer_handles: Sequence[str],
) -> str:
    logical_effect = json.dumps(
        {
            "repo": repo,
            "pr_number": pr_number,
            "sink_kind": sink_kind,
            "sink_instance_key": sink_instance_key,
            "reviewer_handles": sorted(reviewer_handles),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(logical_effect.encode('utf-8')).hexdigest()}"


def reviewer_notification_idempotency_key(
    *,
    repo: str,
    pr_number: int,
    sink_kind: str,
    sink_instance_key: str,
    reviewer_handles: Sequence[str],
) -> str:
    """Return the public logical-effect key used by queue/drain reconciliation."""

    reviewers = list(
        dict.fromkeys(
            handle
            for value in reviewer_handles
            if (handle := _normalise_handle(value)) is not None
        )
    )[:3]
    return _idempotency_key(
        repo=public_safe_compact_text(repo, limit=200),
        pr_number=int(pr_number),
        sink_kind=public_safe_compact_text(sink_kind, limit=50).strip(),
        sink_instance_key=str(sink_instance_key).strip(),
        reviewer_handles=reviewers,
    )


def _parse_delivery_observed_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    text = str(value).strip()
    if not text:
        raise ValueError("delivery_observed_at must not be empty")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("delivery_observed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("delivery_observed_at must include a timezone")
    return parsed


def _delivery_window_decision(
    policy: Any,
    *,
    delivery_observed_at: str | None,
) -> dict[str, Any]:
    if policy is None:
        return {"configured": False, "allowed": True}
    if not isinstance(policy, Mapping):
        raise ValueError("delivery_policy must be an object")
    timezone_name = str(policy.get("timezone") or "").strip()
    allowed_local_time = policy.get("allowed_local_time")
    if not isinstance(allowed_local_time, Mapping):
        raise ValueError("delivery_policy.allowed_local_time must be an object")
    start_text = str(allowed_local_time.get("start") or "").strip()
    end_text = str(allowed_local_time.get("end") or "").strip()
    if (
        not LOCAL_TIME_PATTERN.fullmatch(start_text)
        or not LOCAL_TIME_PATTERN.fullmatch(end_text)
        or start_text == end_text
        or policy.get("outside_window") != "queue_without_send"
    ):
        raise ValueError("delivery_policy window is invalid")
    try:
        location = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("delivery_policy timezone is invalid") from exc

    observed = _parse_delivery_observed_at(delivery_observed_at)
    local = observed.astimezone(location)
    start = time.fromisoformat(start_text)
    end = time.fromisoformat(end_text)
    current = local.timetz().replace(tzinfo=None)
    allowed = (
        start <= current < end if start < end else current >= start or current < end
    )
    decision: dict[str, Any] = {
        "configured": True,
        "allowed": allowed,
        "timezone": timezone_name,
        "start": start_text,
        "end": end_text,
        "observed_at": observed.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    if not allowed:
        next_start = datetime.combine(local.date(), start, tzinfo=location)
        if local >= next_start:
            next_start += timedelta(days=1)
        decision["not_before"] = (
            next_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    return decision


def _queued_result(
    *,
    repo: str,
    pr_number: int,
    sink_kind: str,
    sink_instance_key: str,
    reviewer_handles: Sequence[str],
    message_summary: str,
    summary_policy_status: str,
    execute: bool,
    window: Mapping[str, Any],
) -> dict[str, Any]:
    if not SAFE_LOCAL_KEY_PATTERN.fullmatch(sink_instance_key):
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=[],
            idempotency_key=None,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=execute,
            blocker="reviewer_notification_delivery_policy_invalid",
        )
    key = _idempotency_key(
        repo=repo,
        pr_number=pr_number,
        sink_kind=sink_kind,
        sink_instance_key=sink_instance_key,
        reviewer_handles=reviewer_handles,
    )
    result = build_reviewer_notification_sink_result(
        sink_kind=sink_kind,
        reviewer_handles=reviewer_handles,
        idempotency_key=key,
        status="queued_until_window",
        ok=True,
        external_write_authority_asserted=execute,
    )
    result["queue_receipt"] = {
        "schema_version": (
            ISSUE_FIX_REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION
        ),
        "idempotency_key": key,
        "sink_kind": sink_kind,
        "reviewer_handles": list(reviewer_handles),
        "message_summary": public_safe_compact_text(message_summary, limit=240),
        "summary_policy_status": public_safe_compact_text(
            summary_policy_status, limit=80
        ),
        "queued_at": window["observed_at"],
        "not_before": window["not_before"],
        "timezone": window["timezone"],
        "allowed_local_time": {
            "start": window["start"],
            "end": window["end"],
        },
        "status": "queued",
    }
    return result


def build_reviewer_notification_sink_result(
    *,
    sink_kind: str,
    reviewer_handles: Sequence[str],
    idempotency_key: str | None,
    status: str,
    ok: bool,
    external_write_authority_asserted: bool,
    external_write_performed: bool = False,
    verification_performed: bool = False,
    notification_verified: bool = False,
    bot_identity_verified: bool = False,
    reader_identity_verified: bool = False,
    semantic_dedupe_status: str | None = None,
    blocker: str | None = None,
) -> dict[str, Any]:
    """Build the public-safe result contract returned by provider adapters."""

    result: dict[str, Any] = {
        "ok": ok,
        "schema_version": ISSUE_FIX_REVIEWER_NOTIFICATION_SINK_RESULT_SCHEMA_VERSION,
        "sink_kind": sink_kind,
        "status": status,
        "reviewer_handles": list(reviewer_handles),
        "resolved_reviewer_count": len(reviewer_handles),
        "idempotency_key": idempotency_key,
        "identity_scope": "project_dedicated",
        "external_write_authority_asserted": external_write_authority_asserted,
        "external_write_performed": external_write_performed,
        "verification_performed": verification_performed,
        "notification_verified": notification_verified,
        "bot_identity_verified": bot_identity_verified,
        "reader_identity_verified": reader_identity_verified,
        "private_destination_captured": False,
        "private_member_ids_captured": False,
        "private_bot_profile_captured": False,
        "raw_provider_payload_captured": False,
    }
    if blocker:
        result["blocker"] = blocker
    if semantic_dedupe_status:
        result["semantic_dedupe_status"] = semantic_dedupe_status
    return result


def validate_issue_fix_reviewer_notification_sinks_result(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != (
        ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_RESULT_SCHEMA_VERSION
    ):
        errors.append(
            "schema_version must be issue_fix_reviewer_notification_sinks_result_v0"
        )
    for field in (
        "private_destination_captured",
        "private_member_ids_captured",
        "private_bot_profile_captured",
        "raw_provider_payload_captured",
    ):
        if packet.get(field) is not False:
            errors.append(f"{field} must be false")
    results = packet.get("results")
    if not isinstance(results, list):
        errors.append("results must be a list")
        results = []
    for result in results:
        if not isinstance(result, Mapping):
            errors.append("each sink result must be an object")
            continue
        if result.get("schema_version") != (
            ISSUE_FIX_REVIEWER_NOTIFICATION_SINK_RESULT_SCHEMA_VERSION
        ):
            errors.append("sink result schema_version is invalid")
        for field in (
            "private_destination_captured",
            "private_member_ids_captured",
            "private_bot_profile_captured",
            "raw_provider_payload_captured",
        ):
            if result.get(field) is not False:
                errors.append(f"sink result {field} must be false")
    receipts = packet.get("receipts")
    if not isinstance(receipts, list) or any(
        not ENVELOPED_SHA256_PATTERN.fullmatch(str(value))
        for value in (receipts if isinstance(receipts, list) else [])
    ):
        errors.append("receipts must contain only stable sha256 keys")
    queued_receipts = packet.get("queued_receipts")
    if not isinstance(queued_receipts, list):
        errors.append("queued_receipts must be a list")
        queued_receipts = []
    queued_keys: set[str] = set()
    for receipt in queued_receipts:
        if not isinstance(receipt, Mapping):
            errors.append("each queued receipt must be an object")
            continue
        key = str(receipt.get("idempotency_key") or "")
        window = receipt.get("allowed_local_time")
        if (
            receipt.get("schema_version")
            != ISSUE_FIX_REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION
            or not ENVELOPED_SHA256_PATTERN.fullmatch(key)
            or key in queued_keys
            or receipt.get("status") != "queued"
            or not public_safe_compact_text(receipt.get("sink_kind"), limit=50)
            or not isinstance(receipt.get("reviewer_handles"), list)
            or not public_safe_compact_text(receipt.get("message_summary"), limit=240)
            or receipt.get("summary_policy_status")
            not in {"reward_memory_verified", "sink_config"}
            or not isinstance(window, Mapping)
            or not LOCAL_TIME_PATTERN.fullmatch(str(window.get("start") or ""))
            or not LOCAL_TIME_PATTERN.fullmatch(str(window.get("end") or ""))
        ):
            errors.append("queued receipt is invalid")
            continue
        queued_keys.add(key)
    if isinstance(receipts, list) and queued_keys.intersection(
        str(value) for value in receipts
    ):
        errors.append("verified receipts cannot remain queued")
    writes = packet.get("external_writes_performed") is True
    if writes != any(
        isinstance(result, Mapping) and result.get("external_write_performed") is True
        for result in results
    ):
        errors.append("external_writes_performed must reflect sink results")
    verified = packet.get("notification_verified") is True
    if results and verified != all(
        isinstance(result, Mapping) and result.get("notification_verified") is True
        for result in results
    ):
        errors.append("notification_verified must reflect every sink result")
    return {
        "ok": not errors,
        "schema_version": "issue_fix_reviewer_notification_sinks_validation_v0",
        "errors": errors,
    }


def _finalize_result(packet: dict[str, Any]) -> dict[str, Any]:
    validation = validate_issue_fix_reviewer_notification_sinks_result(packet)
    packet["ok"] = bool(packet.get("ok") and validation["ok"])
    packet["validation"] = validation
    return packet


def build_issue_fix_reviewer_notification_sinks_result(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    pr_title: str | None = None,
    linked_issue_refs: Sequence[str] = (),
    author_handle: str | None,
    reviewer_handles: Sequence[str],
    sinks_input: Mapping[str, Any],
    reviewer_artifact_application: Mapping[str, Any] | None = None,
    reviewer_notification_policy_application: Mapping[str, Any] | None = None,
    reviewer_artifact_required: bool = False,
    execute: bool = False,
    delivery_observed_at: str | None = None,
    runner: CommandRunner = _default_runner,
    sink_adapters: Mapping[str, NotificationSinkAdapter] | None = None,
) -> dict[str, Any]:
    """Preview or execute private-configured secondary reviewer notifications."""

    author = _normalise_handle(author_handle)
    title = _humanize_pr_title(pr_title)
    artifact_gate = reviewer_artifact_notification_gate(reviewer_artifact_application)
    before_send_gate = reviewer_notification_before_send_gate(
        reviewer_notification_policy_application,
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
    )
    artifact = (
        reviewer_artifact_application.get("reviewer_artifact")
        if isinstance(reviewer_artifact_application, Mapping)
        else None
    )
    artifact = artifact if isinstance(artifact, Mapping) else {}
    if artifact_gate["passed"] is True and not (
        artifact.get("repo") == public_safe_compact_text(repo, limit=200)
        and artifact.get("pr_ref") == f"#{int(pr_number)}"
        and artifact.get("permalink") == public_safe_compact_text(pr_url, limit=300)
    ):
        artifact_gate = {
            **artifact_gate,
            "passed": False,
            "status": "blocked",
            "reason_codes": ["current_artifact_identity_mismatch"],
        }
    if artifact_gate["passed"] is True:
        title = str(artifact_gate["summary"])
    issue_refs = _normalise_issue_refs(linked_issue_refs)
    reviewers = list(
        dict.fromkeys(
            handle
            for value in reviewer_handles
            if (handle := _normalise_handle(value)) is not None
        )
    )[:3]
    base: dict[str, Any] = {
        "ok": False,
        "schema_version": ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_RESULT_SCHEMA_VERSION,
        "mode": "issue-fix-reviewer-notification-sinks",
        "repo": public_safe_compact_text(repo, limit=200),
        "pr_ref": f"#{int(pr_number)}",
        "permalink": public_safe_compact_text(pr_url, limit=300),
        "pr_title": title,
        "linked_issue_refs": issue_refs,
        "reviewer_handles": reviewers,
        "status": "gate_required",
        "results": [],
        "receipts": [],
        "queued_receipts": [],
        "external_write_authority_asserted": execute,
        "external_writes_performed": False,
        "notification_verified": False,
        "private_destination_captured": False,
        "private_member_ids_captured": False,
        "private_bot_profile_captured": False,
        "raw_provider_payload_captured": False,
        "reward_memory_reviewer_artifact_required": reviewer_artifact_required,
        "reward_memory_reviewer_artifact_gate": artifact_gate,
        "reward_memory_before_send_gate": before_send_gate,
        "reward_memory_before_send_status": before_send_gate["status"],
    }
    if reviewer_artifact_required and artifact_gate["passed"] is not True:
        base["blocker"] = "reward_memory_reviewer_artifact_unverified"
        return _finalize_result(base)
    if sinks_input.get("schema_version") != (
        ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_INPUT_SCHEMA_VERSION
    ):
        base["blocker"] = "reviewer_notification_sinks_input_invalid"
        return _finalize_result(base)
    if not author:
        base["blocker"] = "reviewer_notification_author_unavailable"
        return _finalize_result(base)
    if not reviewers:
        base["blocker"] = "reviewer_notification_reviewer_unavailable"
        return _finalize_result(base)
    if author in reviewers:
        base["blocker"] = "reviewer_notification_author_exclusion_failed"
        return _finalize_result(base)

    raw_sinks = sinks_input.get("sinks")
    sinks = raw_sinks if isinstance(raw_sinks, list) else []
    if (
        not sinks
        or len(sinks) > 3
        or not all(isinstance(sink, Mapping) for sink in sinks)
    ):
        base["blocker"] = "reviewer_notification_sinks_input_invalid"
        return _finalize_result(base)
    raw_receipts = sinks_input.get("receipts")
    receipts = {
        str(value)
        for value in (raw_receipts if isinstance(raw_receipts, list) else [])
        if ENVELOPED_SHA256_PATTERN.fullmatch(str(value))
    }
    semantic_history_pr_refs = {
        public_safe_compact_text(value, limit=300)
        for value in (
            sinks_input.get("_semantic_history_pr_refs")
            if isinstance(sinks_input.get("_semantic_history_pr_refs"), list)
            else []
        )
        if public_safe_compact_text(value, limit=300)
    }
    queued_receipts_by_key = {
        str(value["idempotency_key"]): value
        for value in reviewer_notification_queue_from_state(
            {"reviewer_notification_queue": sinks_input.get("queued_receipts")}
        )
    }
    effective_delivery_policy = sinks_input.get("delivery_policy")
    if before_send_gate["passed"] is True:
        effective_delivery_policy = before_send_gate["delivery_policy"]
    try:
        delivery_window = _delivery_window_decision(
            effective_delivery_policy,
            delivery_observed_at=delivery_observed_at,
        )
    except ValueError:
        base["blocker"] = "reviewer_notification_delivery_policy_invalid"
        return _finalize_result(base)
    base["delivery_policy_configured"] = delivery_window["configured"]
    base["delivery_policy_source"] = (
        "reward_memory"
        if before_send_gate["passed"] is True
        else "sink_config"
        if sinks_input.get("delivery_policy") is not None
        else "default_unrestricted"
    )

    adapters = dict(sink_adapters or {})
    results: list[dict[str, Any]] = []
    for sink in sinks:
        sink_kind = str(sink.get("sink_kind") or "").strip()
        sink_instance_key = str(sink.get("sink_instance_key") or "").strip()
        current_key = (
            _idempotency_key(
                repo=repo,
                pr_number=int(pr_number),
                sink_kind=sink_kind,
                sink_instance_key=sink_instance_key,
                reviewer_handles=reviewers,
            )
            if sink_kind and SAFE_LOCAL_KEY_PATTERN.fullmatch(sink_instance_key)
            else None
        )
        if pr_url in semantic_history_pr_refs:
            if current_key:
                receipts.add(current_key)
        adapter = adapters.get(sink_kind)
        if adapter and execute and not delivery_window["allowed"]:
            queued_key = current_key
            if queued_key and queued_key in receipts:
                result = build_reviewer_notification_sink_result(
                    sink_kind=sink_kind,
                    reviewer_handles=reviewers,
                    idempotency_key=queued_key,
                    status="already_notified",
                    ok=True,
                    external_write_authority_asserted=execute,
                    notification_verified=True,
                )
            elif queued_key and queued_key in queued_receipts_by_key:
                result = build_reviewer_notification_sink_result(
                    sink_kind=sink_kind,
                    reviewer_handles=reviewers,
                    idempotency_key=queued_key,
                    status="already_queued",
                    ok=True,
                    external_write_authority_asserted=execute,
                )
                result["queue_receipt"] = dict(queued_receipts_by_key[queued_key])
            else:
                result = _queued_result(
                    repo=repo,
                    pr_number=int(pr_number),
                    sink_kind=sink_kind,
                    sink_instance_key=sink_instance_key,
                    reviewer_handles=reviewers,
                    message_summary=title or "待代码审查",
                    summary_policy_status=(
                        "reward_memory_verified"
                        if artifact_gate["passed"] is True
                        else "sink_config"
                    ),
                    execute=execute,
                    window=delivery_window,
                )
        elif adapter:
            result = adapter(
                repo=repo,
                pr_number=int(pr_number),
                pr_url=pr_url,
                pr_title=title,
                linked_issue_refs=issue_refs,
                reviewer_handles=reviewers,
                sink=sink,
                receipts=receipts,
                execute=execute,
                runner=runner,
            )
        else:
            result = build_reviewer_notification_sink_result(
                sink_kind=public_safe_compact_text(sink_kind, limit=50) or "unknown",
                reviewer_handles=[],
                idempotency_key=None,
                status="gate_required",
                ok=False,
                external_write_authority_asserted=execute,
                blocker="reviewer_notification_sink_unsupported",
            )
        if result.get("idempotency_key") is None and current_key:
            result = {**result, "idempotency_key": current_key}
        results.append(result)

    successful_receipts = list(
        dict.fromkeys(
            result["idempotency_key"]
            for result in results
            if result.get("notification_verified") is True
            and result.get("idempotency_key")
        )
    )
    queued_receipts = list(
        {
            str(receipt["idempotency_key"]): receipt
            for result in results
            if isinstance(result.get("queue_receipt"), Mapping)
            for receipt in [dict(result["queue_receipt"])]
        }.values()
    )
    queued_receipts_by_result_key = {
        str(receipt["idempotency_key"]): receipt for receipt in queued_receipts
    }
    for result in results:
        key = str(result.get("idempotency_key") or "")
        prior = queued_receipts_by_key.get(key)
        if (
            prior is not None
            and result.get("ok") is False
            and result.get("external_write_performed") is not True
            and key not in queued_receipts_by_result_key
        ):
            queued_receipts.append(dict(prior))
            queued_receipts_by_result_key[key] = dict(prior)
    statuses = {str(result.get("status")) for result in results}
    if len(statuses) == 1:
        status = statuses.pop()
    elif any(result.get("ok") is False for result in results):
        status = "partial_failure"
    elif queued_receipts:
        status = "partial_queued"
    elif execute:
        status = "sent_verified"
    else:
        status = "preview_ready"
    base.update(
        {
            "ok": all(result.get("ok") is True for result in results),
            "status": status,
            "results": results,
            "receipts": successful_receipts,
            "queued_receipts": queued_receipts,
            "external_writes_performed": any(
                result.get("external_write_performed") is True for result in results
            ),
            "notification_verified": all(
                result.get("notification_verified") is True for result in results
            ),
            "semantic_history_evidence_applied": bool(
                pr_url in semantic_history_pr_refs
            ),
        }
    )
    blockers = [
        str(result.get("blocker")) for result in results if result.get("blocker")
    ]
    if blockers:
        base["blocker"] = blockers[0] if len(set(blockers)) == 1 else "multiple"
    return _finalize_result(base)
