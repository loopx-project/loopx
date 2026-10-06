from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .control_plane.runtime.time import parse_timestamp as _parse_timestamp
from .control_plane.todos.contract import normalize_required_write_scopes
from .public_safe_text import (
    PRIVATE_TEXT_PATTERNS as SHARED_PRIVATE_TEXT_PATTERNS,
    TEXT_OWNER_CATEGORIES,
    classify_private_text,
)


CHECKPOINTED_BOUNDARY_AUTHORITY_SCHEMA_VERSION = "checkpointed_boundary_authority_v0"
ACTIVE_BOUNDARY_AUTHORITY_STATUSES = {"active", "approved"}
BOUNDARY_AUTHORITY_DECISIONS = {"approve", "reject", "defer"}
# Owned by loopx.public_safe_text so every real validator owner shares one
# contract; re-exported here because callers already import this name.
PRIVATE_TEXT_PATTERNS = SHARED_PRIVATE_TEXT_PATTERNS


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _validate_public_safe_text(label: str, value: str | None) -> None:
    # Checkpointed lease text is LoopX's own state, so it uses the internal-state
    # policy: the words bearer/password/secret are recognized, not rejected, while
    # every shape that carries a value still fails (Refs #5136, direction 2).
    if classify_private_text(value, categories=TEXT_OWNER_CATEGORIES) is not None:
        raise ValueError(f"{label} contains a private-looking value; keep raw evidence in private payloads")


def _clean_text(value: Any, *, limit: int = 180) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    _validate_public_safe_text("checkpointed_boundary_authority", text)
    return text[:limit]


def build_checkpointed_boundary_authority_entry(
    *,
    write_scopes: list[str],
    source: str,
    decision_id: str | None = None,
    recorded_at: str | None = None,
    expires_at: str | None = None,
    decision: str = "approve",
    status: str = "active",
) -> dict[str, Any]:
    scopes = normalize_required_write_scopes(write_scopes)
    if not scopes:
        raise ValueError("boundary authority requires at least one public-safe write scope")
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in BOUNDARY_AUTHORITY_DECISIONS:
        raise ValueError(
            "boundary authority decision must be one of: "
            + ", ".join(sorted(BOUNDARY_AUTHORITY_DECISIONS))
        )
    normalized_source = _clean_text(source)
    if not normalized_source:
        raise ValueError("boundary authority source is required")
    normalized_decision_id = _clean_text(decision_id, limit=120)
    normalized_status = str(status or "active").strip().lower() or "active"
    recorded = str(recorded_at or _now().replace(microsecond=0).isoformat()).strip()
    if not _parse_timestamp(recorded):
        raise ValueError("boundary authority recorded_at must be an ISO timestamp")
    entry: dict[str, Any] = {
        "schema_version": CHECKPOINTED_BOUNDARY_AUTHORITY_SCHEMA_VERSION,
        "status": normalized_status,
        "decision": normalized_decision,
        "write_scope": scopes,
        "source": normalized_source,
        "recorded_at": recorded,
    }
    if normalized_decision_id:
        entry["decision_id"] = normalized_decision_id
    if expires_at:
        if not _parse_timestamp(expires_at):
            raise ValueError("boundary authority expires_at must be an ISO timestamp")
        entry["expires_at"] = str(expires_at).strip()
    return entry


def normalize_checkpointed_boundary_authority_entries(
    value: Any,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    current_time = now or _now()
    entries: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        scopes = normalize_required_write_scopes(
            raw.get("write_scope")
            or raw.get("write_scopes")
            or raw.get("scope")
            or raw.get("scopes")
        )
        decision = str(raw.get("decision") or raw.get("operator_decision") or "approve").strip().lower()
        if decision not in BOUNDARY_AUTHORITY_DECISIONS:
            decision = "defer"
        status = str(raw.get("status") or "active").strip().lower() or "active"
        source = _clean_text(raw.get("source") or raw.get("provenance") or raw.get("reason_summary"))
        decision_id = _clean_text(raw.get("decision_id") or raw.get("run_id") or raw.get("gate_id"), limit=120)
        recorded_at = str(raw.get("recorded_at") or raw.get("decision_at") or "").strip()
        # Null/blank retains the optional no-expiry contract. A supplied invalid
        # value (including a false-like non-string) must not become no expiry.
        expires_at = ""
        for field in ("expires_at", "fresh_until"):
            raw_expiry = raw.get(field)
            if raw_expiry is not None:
                expires_at = str(raw_expiry).strip()
                if expires_at:
                    break
        recorded = _parse_timestamp(recorded_at)
        try:
            expires = _parse_timestamp(expires_at)
        except OverflowError:
            # An ISO offset can place an instant outside datetime's UTC range.
            expires = None
        inactive_reasons: list[str] = []
        if not scopes:
            inactive_reasons.append("missing_write_scope")
        if not source:
            inactive_reasons.append("missing_provenance")
        if not recorded:
            inactive_reasons.append("missing_recorded_at")
        if status not in ACTIVE_BOUNDARY_AUTHORITY_STATUSES:
            inactive_reasons.append("inactive_status")
        if decision != "approve":
            inactive_reasons.append("decision_not_approved")
        if expires_at and (not isinstance(raw_expiry, str) or expires is None):
            inactive_reasons.append("invalid_expires_at")
        elif expires and expires < current_time:
            inactive_reasons.append("expired")
        entry: dict[str, Any] = {
            "schema_version": CHECKPOINTED_BOUNDARY_AUTHORITY_SCHEMA_VERSION,
            "status": status,
            "decision": decision,
            "write_scope": scopes,
            "source": source,
            "recorded_at": recorded_at or None,
            "expires_at": expires_at or None,
            "freshness": (
                "invalid" if "invalid_expires_at" in inactive_reasons
                else "expired" if "expired" in inactive_reasons else "fresh"
            ),
            "active": not inactive_reasons,
        }
        if decision_id:
            entry["decision_id"] = decision_id
        if inactive_reasons:
            entry["inactive_reasons"] = inactive_reasons
        entries.append({key: payload for key, payload in entry.items() if payload is not None})
    return entries


def _compact_checkpointed_boundary_authority_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    def _count(raw: Any) -> int:
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    active_scopes = normalize_required_write_scopes(value.get("active_write_scope"))
    if not active_scopes and "active_count" not in value and "inactive_count" not in value:
        return None
    return {
        "schema_version": CHECKPOINTED_BOUNDARY_AUTHORITY_SCHEMA_VERSION,
        "active_count": _count(value.get("active_count")),
        "inactive_count": _count(value.get("inactive_count")),
        "active_write_scope": active_scopes,
    }


def checkpointed_boundary_authority_summary(coordination: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(coordination, dict):
        return None
    checkpointed_authority = coordination.get("checkpointed_boundary_authority")
    if isinstance(checkpointed_authority, dict):
        compact_summary = _compact_checkpointed_boundary_authority_summary(checkpointed_authority)
        if compact_summary is not None and "entries" not in checkpointed_authority:
            return compact_summary
        checkpointed_authority = checkpointed_authority.get("entries")
    entries = normalize_checkpointed_boundary_authority_entries(
        checkpointed_authority
    )
    if not entries:
        return None
    active_entries = [entry for entry in entries if entry.get("active") is True]
    active_scopes: list[str] = []
    for entry in active_entries:
        for scope in normalize_required_write_scopes(entry.get("write_scope")):
            if scope not in active_scopes:
                active_scopes.append(scope)
    return {
        "schema_version": CHECKPOINTED_BOUNDARY_AUTHORITY_SCHEMA_VERSION,
        "active_count": len(active_entries),
        "inactive_count": len(entries) - len(active_entries),
        "active_write_scope": active_scopes,
        "entries": entries,
    }
