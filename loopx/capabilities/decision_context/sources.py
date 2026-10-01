"""Provider-neutral incremental source contracts for Decision Context."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ...public_safe_text import COMPACT_TOKEN_PATTERN as _TOKEN_RE

DECISION_SOURCE_MANIFEST_SCHEMA_VERSION = "decision_source_manifest_v0"
DECISION_SOURCE_SCAN_RECEIPT_SCHEMA_VERSION = "decision_source_scan_receipt_v0"

_SOURCE_KINDS = {
    "artifact",
    "conversation",
    "document",
    "external_signal",
    "person",
    "repository",
}
_PRIORITIES = {"p0", "p1", "p2"}
_EVIDENCE_LEVELS = {"s0", "s1", "s2", "s3", "s4"}
_SCAN_MODES = {"incremental", "on_demand", "snapshot"}
_EXACT_READ_POLICIES = {"always", "material_change", "never"}
_SCAN_STATUSES = {"completed", "failed", "no_change", "unavailable"}


def _token(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(
            f"{field_name} must be a compact token containing only letters, "
            "digits, dot, colon, dash, or underscore"
        )
    return text


def _timestamp(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return text


def _positive_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _optional_token(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _token(value, field_name=field_name)


def _opaque_ref(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _packet_ref(prefix: str, packet: dict[str, object]) -> str:
    payload = json.dumps(
        packet,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _opaque_ref(prefix, payload)


def build_decision_source_item_refs(
    *,
    provider_id: str,
    source_id: str,
    item: DecisionSourceItem,
) -> dict[str, str]:
    """Build stable public-safe refs for one transient authority item."""

    provider_id = _token(provider_id, field_name="provider_id")
    source_id = _token(source_id, field_name="source_id")
    return {
        "source_ref": _opaque_ref(
            "source-change",
            provider_id,
            source_id,
            item.resource_ref,
        ),
        "source_revision": _opaque_ref(
            "source-revision",
            provider_id,
            source_id,
            item.resource_ref,
            item.revision,
        ),
    }


@dataclass(frozen=True)
class DecisionSourceSpec:
    """Private provider configuration with a public-safe manifest projection."""

    source_id: str
    provider_id: str
    source_kind: str
    priority: str
    evidence_level: str
    objectives: tuple[str, ...]
    scan_mode: str
    exact_read_policy: str
    freshness_seconds: int
    private_locator: str = field(repr=False)
    max_changes: int = 20
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id", _token(self.source_id, field_name="source_id")
        )
        object.__setattr__(
            self, "provider_id", _token(self.provider_id, field_name="provider_id")
        )
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError(f"source_kind must be one of {sorted(_SOURCE_KINDS)}")
        if self.priority not in _PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(_PRIORITIES)}")
        if self.evidence_level not in _EVIDENCE_LEVELS:
            raise ValueError(
                f"evidence_level must be one of {sorted(_EVIDENCE_LEVELS)}"
            )
        objectives = tuple(
            sorted(
                {
                    _token(objective, field_name="objectives[]")
                    for objective in self.objectives
                }
            )
        )
        if not objectives:
            raise ValueError("objectives must contain at least one compact token")
        object.__setattr__(self, "objectives", objectives)
        if self.scan_mode not in _SCAN_MODES:
            raise ValueError(f"scan_mode must be one of {sorted(_SCAN_MODES)}")
        if self.exact_read_policy not in _EXACT_READ_POLICIES:
            raise ValueError(
                f"exact_read_policy must be one of {sorted(_EXACT_READ_POLICIES)}"
            )
        object.__setattr__(
            self,
            "freshness_seconds",
            _positive_int(self.freshness_seconds, field_name="freshness_seconds"),
        )
        object.__setattr__(
            self,
            "max_changes",
            _positive_int(self.max_changes, field_name="max_changes"),
        )
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        if not str(self.private_locator).strip():
            raise ValueError("private_locator must be non-empty")

    def public_record(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "provider_id": self.provider_id,
            "source_kind": self.source_kind,
            "priority": self.priority,
            "evidence_level": self.evidence_level,
            "objectives": list(self.objectives),
            "scan_mode": self.scan_mode,
            "exact_read_policy": self.exact_read_policy,
            "freshness_seconds": self.freshness_seconds,
            "max_changes": self.max_changes,
            "enabled": self.enabled,
            "private_locator_captured": False,
        }


@dataclass(frozen=True)
class DecisionSourceItem:
    """One transient changed item returned by a source provider."""

    resource_ref: str = field(repr=False)
    revision: str = field(repr=False)
    observed_at: str

    def __post_init__(self) -> None:
        if not str(self.resource_ref).strip():
            raise ValueError("resource_ref must be non-empty")
        if not str(self.revision).strip():
            raise ValueError("revision must be non-empty")
        _timestamp(self.observed_at, field_name="observed_at")


@dataclass(frozen=True)
class DecisionSourceExactRead:
    """Transient exact content used by the agent, never by public packets."""

    resource_ref: str = field(repr=False)
    revision: str = field(repr=False)
    observed_at: str
    content: str = field(repr=False)
    verified: bool = True

    def __post_init__(self) -> None:
        if not str(self.resource_ref).strip():
            raise ValueError("resource_ref must be non-empty")
        if not str(self.revision).strip():
            raise ValueError("revision must be non-empty")
        _timestamp(self.observed_at, field_name="observed_at")
        if not str(self.content).strip():
            raise ValueError("content must be non-empty")
        if not isinstance(self.verified, bool):
            raise TypeError("verified must be a boolean")


@dataclass(frozen=True)
class DecisionSourceScan:
    """One bounded source scan with private cursors and transient items."""

    provider_id: str
    source_id: str
    status: str
    observed_at: str
    requested_limit: int
    cursor_before: str | None = field(default=None, repr=False)
    cursor_after: str | None = field(default=None, repr=False)
    items: tuple[DecisionSourceItem, ...] = ()
    reason_code: str | None = None
    provider_version: str | None = None
    latency_ms: int = 0

    def public_receipt(
        self,
        *,
        exact_reads: Sequence[DecisionSourceExactRead] = (),
    ) -> dict[str, object]:
        provider_id = _token(self.provider_id, field_name="provider_id")
        source_id = _token(self.source_id, field_name="source_id")
        observed_at = _timestamp(self.observed_at, field_name="observed_at")
        requested_limit = _positive_int(
            self.requested_limit, field_name="requested_limit"
        )
        reason_code = _optional_token(self.reason_code, field_name="reason_code")
        provider_version = _optional_token(
            self.provider_version,
            field_name="provider_version",
        )
        latency_ms = _non_negative_int(self.latency_ms, field_name="latency_ms")
        if self.status not in _SCAN_STATUSES:
            raise ValueError(f"status must be one of {sorted(_SCAN_STATUSES)}")
        if self.status in {"failed", "unavailable"} and reason_code is None:
            raise ValueError("failed or unavailable scans require reason_code")
        if self.status in {"failed", "no_change", "unavailable"} and (
            self.items or exact_reads
        ):
            raise ValueError(
                "failed, no-change, or unavailable scans cannot contain "
                "items or exact reads"
            )
        if len(self.items) > requested_limit:
            raise ValueError("scan items cannot exceed requested_limit")

        read_keys = {
            (read.resource_ref, read.revision) for read in exact_reads if read.verified
        }
        item_keys = {(item.resource_ref, item.revision) for item in self.items}
        if len(item_keys) != len(self.items):
            raise ValueError("scan items must have unique resource and revision pairs")
        if not read_keys <= item_keys:
            raise ValueError("exact reads must reference an item from this scan")

        changes = []
        for item in self.items:
            item_observed_at = _timestamp(
                item.observed_at, field_name="item.observed_at"
            )
            refs = build_decision_source_item_refs(
                provider_id=provider_id,
                source_id=source_id,
                item=item,
            )
            changes.append(
                {
                    "change_ref": refs["source_ref"],
                    "revision_ref": refs["source_revision"],
                    "observed_at": item_observed_at,
                    "exact_read_verified": (
                        item.resource_ref,
                        item.revision,
                    )
                    in read_keys,
                }
            )
        changes.sort(key=lambda record: str(record["change_ref"]))

        packet: dict[str, object] = {
            "schema_version": DECISION_SOURCE_SCAN_RECEIPT_SCHEMA_VERSION,
            "capability_id": "decision_context",
            "scope": "goal",
            "default_enabled": False,
            "provider_id": provider_id,
            "source_id": source_id,
            "status": self.status,
            "reason_code": reason_code,
            "provider_version": provider_version,
            "observed_at": observed_at,
            "requested_limit": requested_limit,
            "changed_count": len(changes),
            "exact_read_count": len(read_keys),
            "changes": changes,
            "cursor_before_ref": (
                _opaque_ref("source-cursor", provider_id, source_id, self.cursor_before)
                if self.cursor_before
                else None
            ),
            "cursor_after_ref": (
                _opaque_ref("source-cursor", provider_id, source_id, self.cursor_after)
                if self.cursor_after
                else None
            ),
            "provider_fail_open": True,
            "external_writes_performed": False,
            "raw_content_captured": False,
            "raw_provider_payload_captured": False,
            "private_locator_captured": False,
            "private_cursor_captured": False,
            "credentials_captured": False,
            "telemetry": {
                "latency_ms": latency_ms,
                "result_cap_applied": len(changes) == requested_limit,
            },
        }
        packet["receipt_ref"] = _packet_ref("decision-source-scan", packet)
        return packet


class DecisionSourceProvider(Protocol):
    """Change detection and exact read for an authority source.

    This is intentionally separate from ``ContextProvider``: source providers
    rebase current authority, while context providers recall advisory memory.
    """

    provider_id: str

    def scan(
        self,
        *,
        source: DecisionSourceSpec,
        after_cursor: str | None,
        before: str,
        limit: int,
        timeout_seconds: float,
        observed_at: str,
    ) -> DecisionSourceScan: ...

    def exact_read(
        self,
        *,
        source: DecisionSourceSpec,
        item: DecisionSourceItem,
        timeout_seconds: float,
        observed_at: str,
    ) -> DecisionSourceExactRead: ...


def build_decision_source_manifest(
    *,
    goal_id: str,
    observed_at: str,
    sources: Sequence[DecisionSourceSpec],
) -> dict[str, object]:
    """Build a deterministic public projection of private source config."""

    goal_id = _token(goal_id, field_name="goal_id")
    observed_at = _timestamp(observed_at, field_name="observed_at")
    records = [source.public_record() for source in sources]
    records.sort(key=lambda record: str(record["source_id"]))
    source_ids = [str(record["source_id"]) for record in records]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source_id values must be unique")

    packet: dict[str, object] = {
        "schema_version": DECISION_SOURCE_MANIFEST_SCHEMA_VERSION,
        "capability_id": "decision_context",
        "scope": "goal",
        "default_enabled": False,
        "goal_id": goal_id,
        "observed_at": observed_at,
        "sources": records,
        "source_count": len(records),
        "creates_authority": False,
        "mutates_core_state": False,
        "external_writes_performed": False,
        "private_locators_captured": False,
        "private_cursors_captured": False,
    }
    packet["manifest_ref"] = _packet_ref("decision-source-manifest", packet)
    return packet
