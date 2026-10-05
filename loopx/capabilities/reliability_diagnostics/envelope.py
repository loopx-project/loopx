"""Provider-neutral L1 shadow-observer envelope contract.

An observer envelope is the only record a harness event source may hand to
LoopX reliability diagnostics. It is intentionally narrow: identity, a
monotonic sequence, a declared clock, one typed event kind, a compact summary
of tokens and counters, and id-only source references. Every other field is
rejected and classified, so control-shaped or raw-material-shaped records can
never enter the diagnostic ledger.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Any

from ...control_plane.runtime.public_safety import (
    normalize_public_safe_field_name,
    validate_public_safe_value,
)
from ...session_runtime import SOURCE_ID_KEYS

CAPABILITY_ID = "reliability-diagnostics"
DSH_PROVIDER_ID = "dsh-session-events"
OBSERVER_ENVELOPE_SCHEMA_VERSION = "reliability_observer_envelope_v0"
OBSERVER_STATS_SCHEMA_VERSION = "reliability_observer_stats_v0"

IDENTITY_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$")
SUMMARY_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,79}$")
MAX_SEQUENCE = 2**53


class ObserverEventKind(StrEnum):
    """Compact, provider-neutral event kinds a shadow observer may report."""

    SESSION_STARTED = "session_started"
    TURN_STARTED = "turn_started"
    TURN_ENDED = "turn_ended"
    STEP_STARTED = "step_started"
    STEP_ENDED = "step_ended"
    USER_MESSAGE = "user_message"
    TOOL_CALLED = "tool_called"
    TOOL_COMPLETED = "tool_completed"
    AGENT_STATUS = "agent_status"
    AGENT_PRE_STEP = "agent_pre_step"
    AGENT_ERROR = "agent_error"
    SESSION_DISPOSED = "session_disposed"
    UNSUPPORTED = "unsupported"


class ClockSource(StrEnum):
    HARNESS_EVENT_TIME = "harness_event_time"
    OBSERVER_WALL_CLOCK = "observer_wall_clock"
    FIXTURE = "fixture"


class EnvelopeRejection(StrEnum):
    """Typed reasons an envelope is refused; each maps to a receipt counter."""

    SCHEMA_MISMATCH = "schema_mismatch"
    CONTROL_FIELD_REJECTED = "control_field_rejected"
    RAW_MATERIAL_FIELD_REJECTED = "raw_material_field_rejected"
    UNSUPPORTED_FIELD_REJECTED = "unsupported_field_rejected"
    IDENTITY_INVALID = "identity_invalid"
    SEQUENCE_INVALID = "sequence_invalid"
    CLOCK_INVALID = "clock_invalid"
    EVENT_KIND_INVALID = "event_kind_invalid"
    SUMMARY_INVALID = "summary_invalid"
    SOURCE_REF_INVALID = "source_ref_invalid"
    PUBLIC_SAFETY_VIOLATION = "public_safety_violation"
    OBSERVER_INTERNAL_FAILURE = "observer_internal_failure"


ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "capability_id",
        "provider_id",
        "observer_id",
        "goal_id",
        "session_id",
        "agent_id",
        "sequence",
        "observed_at",
        "clock",
        "event_kind",
        "summary",
        "source_refs",
    }
)
CLOCK_FIELDS = frozenset({"source", "uncertainty_ms"})
SUMMARY_INTEGER_FIELDS = frozenset({"turn", "step"})
SUMMARY_TOKEN_FIELDS = frozenset(
    {
        "reason",
        "status",
        "tool_name",
        "error_class",
        "source_event_type",
        "message_source_kind",
    }
)
SUMMARY_FIELDS = SUMMARY_INTEGER_FIELDS | SUMMARY_TOKEN_FIELDS
SOURCE_REF_FIELDS = (frozenset(SOURCE_ID_KEYS) - {"session_id"}) | {"event_seq", "message_id"}

# Exact field families (after public-safe key normalization) that reveal an
# outbound control path. Their presence is a contract violation, never data.
CONTROL_FIELD_FAMILIES = frozenset(
    {
        "command",
        "commands",
        "send",
        "prompt",
        "inject",
        "schedule",
        "retry",
        "stop",
        "resume",
        "pause",
        "cancel",
        "gate",
        "gatedecision",
        "toolcall",
        "toolinvocation",
        "workerstate",
        "continuation",
        "callback",
        "endpoint",
        "outboundendpoint",
    }
)
# Exact field families that carry protected task content or local material.
RAW_MATERIAL_FIELD_FAMILIES = frozenset(
    {
        "transcript",
        "messages",
        "content",
        "text",
        "arguments",
        "result",
        "output",
        "tooloutput",
        "stdout",
        "stderr",
        "log",
        "logs",
        "trace",
        "path",
        "localpath",
        "cwd",
        "credential",
        "credentials",
        "token",
        "secret",
    }
)


class ObserverEnvelopeError(ValueError):
    def __init__(self, reason: EnvelopeRejection, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ObserverClock:
    source: ClockSource
    uncertainty_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source.value, "uncertainty_ms": self.uncertainty_ms}


@dataclass(frozen=True)
class ObserverEnvelope:
    provider_id: str
    observer_id: str
    goal_id: str
    session_id: str
    sequence: int
    observed_at: str
    clock: ObserverClock
    event_kind: ObserverEventKind
    agent_id: str | None = None
    summary: Mapping[str, int | str] = field(default_factory=dict)
    source_refs: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": OBSERVER_ENVELOPE_SCHEMA_VERSION,
            "capability_id": CAPABILITY_ID,
            "provider_id": self.provider_id,
            "observer_id": self.observer_id,
            "goal_id": self.goal_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "observed_at": self.observed_at,
            "clock": self.clock.as_dict(),
            "event_kind": self.event_kind.value,
            "summary": dict(self.summary),
            "source_refs": dict(self.source_refs),
        }
        if self.agent_id is not None:
            record["agent_id"] = self.agent_id
        return record


def classify_rejected_field(key: object) -> EnvelopeRejection:
    normalized = normalize_public_safe_field_name(key).replace("_", "")
    if normalized in CONTROL_FIELD_FAMILIES:
        return EnvelopeRejection.CONTROL_FIELD_REJECTED
    if normalized in RAW_MATERIAL_FIELD_FAMILIES:
        return EnvelopeRejection.RAW_MATERIAL_FIELD_REJECTED
    return EnvelopeRejection.UNSUPPORTED_FIELD_REJECTED


def _reject_unknown_fields(
    value: Mapping[str, Any], *, allowed: frozenset[str], context: str
) -> None:
    unknown = [str(key) for key in value if str(key) not in allowed]
    if not unknown:
        return
    reasons = sorted(
        (classify_rejected_field(key) for key in unknown),
        key=lambda reason: (
            reason is not EnvelopeRejection.CONTROL_FIELD_REJECTED,
            reason is not EnvelopeRejection.RAW_MATERIAL_FIELD_REJECTED,
        ),
    )
    raise ObserverEnvelopeError(
        reasons[0], f"{context} carries {len(unknown)} unsupported field(s)"
    )


def _identity(value: Any, *, name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not IDENTITY_TOKEN_PATTERN.match(value):
        raise ObserverEnvelopeError(
            EnvelopeRejection.IDENTITY_INVALID, f"{name} must be an identity token"
        )
    return value


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < MAX_SEQUENCE:
        raise ObserverEnvelopeError(
            EnvelopeRejection.SEQUENCE_INVALID,
            "sequence must be a non-negative integer",
        )
    return value


def _observed_at(value: Any) -> str:
    if not isinstance(value, str):
        raise ObserverEnvelopeError(
            EnvelopeRejection.CLOCK_INVALID, "observed_at must be ISO-8601 text"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObserverEnvelopeError(
            EnvelopeRejection.CLOCK_INVALID, "observed_at is not ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        raise ObserverEnvelopeError(
            EnvelopeRejection.CLOCK_INVALID, "observed_at must carry a timezone"
        )
    return value


def parse_observed_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def observed_at_microseconds(value: str) -> int | Fraction:
    """Exact UTC microseconds, including precision datetime would truncate."""

    parsed = parse_observed_at(value)
    elapsed = parsed - datetime.min.replace(tzinfo=timezone.utc)
    key: int | Fraction = (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    zone_start = max(value.rfind("+"), value.rfind("-"), value.rfind("Z"))
    for match in re.finditer(r"[.,](\d+)", value):
        digits = match.group(1)
        remainder = digits[6:]
        offset_fraction = match.start() > zone_start
        # fromisoformat also discards the entire fraction of a zero-second offset.
        if offset_fraction and not parsed.utcoffset():
            adjustment = Fraction(Decimal("0." + digits)) * 1_000_000
        elif remainder:
            adjustment = Fraction(Decimal("0." + remainder))
        else:
            continue
        key += -adjustment if offset_fraction and value[zone_start] == "+" else adjustment
    return key


def _clock(value: Any) -> ObserverClock:
    if not isinstance(value, Mapping):
        raise ObserverEnvelopeError(
            EnvelopeRejection.CLOCK_INVALID, "clock must declare source and uncertainty"
        )
    _reject_unknown_fields(value, allowed=CLOCK_FIELDS, context="clock")
    try:
        source = ClockSource(str(value.get("source")))
    except ValueError as exc:
        raise ObserverEnvelopeError(
            EnvelopeRejection.CLOCK_INVALID, "clock source is not a declared source"
        ) from exc
    uncertainty = value.get("uncertainty_ms")
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, int) or uncertainty < 0:
        raise ObserverEnvelopeError(
            EnvelopeRejection.CLOCK_INVALID,
            "clock uncertainty_ms must be a non-negative integer",
        )
    return ObserverClock(source=source, uncertainty_ms=uncertainty)


def _event_kind(value: Any) -> ObserverEventKind:
    try:
        return ObserverEventKind(str(value))
    except ValueError as exc:
        raise ObserverEnvelopeError(
            EnvelopeRejection.EVENT_KIND_INVALID, "event_kind is not a typed kind"
        ) from exc


def _summary(value: Any) -> dict[str, int | str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ObserverEnvelopeError(
            EnvelopeRejection.SUMMARY_INVALID, "summary must be an object"
        )
    _reject_unknown_fields(value, allowed=SUMMARY_FIELDS, context="summary")
    compact: dict[str, int | str] = {}
    for key, item in value.items():
        name = str(key)
        if name in SUMMARY_INTEGER_FIELDS:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ObserverEnvelopeError(
                    EnvelopeRejection.SUMMARY_INVALID,
                    f"summary.{name} must be a non-negative integer",
                )
        elif not isinstance(item, str) or not SUMMARY_TOKEN_PATTERN.match(item):
            raise ObserverEnvelopeError(
                EnvelopeRejection.SUMMARY_INVALID,
                f"summary.{name} must be a compact token",
            )
        compact[name] = item
    return compact


def _source_refs(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ObserverEnvelopeError(
            EnvelopeRejection.SOURCE_REF_INVALID, "source_refs must be an object"
        )
    _reject_unknown_fields(value, allowed=SOURCE_REF_FIELDS, context="source_refs")
    refs: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not IDENTITY_TOKEN_PATTERN.match(item):
            raise ObserverEnvelopeError(
                EnvelopeRejection.SOURCE_REF_INVALID,
                f"source_refs.{key} must be an identity token",
            )
        refs[str(key)] = item
    return refs


def normalize_observer_envelope(record: Mapping[str, Any]) -> ObserverEnvelope:
    """Validate one observer record and return its typed envelope.

    Rejection order is deliberate: unknown top-level fields are classified
    first so a control-shaped record is reported as a control violation even
    when the rest of the record is malformed.
    """

    if not isinstance(record, Mapping):
        raise ObserverEnvelopeError(
            EnvelopeRejection.SCHEMA_MISMATCH, "envelope must be an object"
        )
    _reject_unknown_fields(record, allowed=ENVELOPE_FIELDS, context="envelope")
    if record.get("schema_version") != OBSERVER_ENVELOPE_SCHEMA_VERSION:
        raise ObserverEnvelopeError(
            EnvelopeRejection.SCHEMA_MISMATCH,
            f"schema_version must be {OBSERVER_ENVELOPE_SCHEMA_VERSION}",
        )
    if record.get("capability_id") != CAPABILITY_ID:
        raise ObserverEnvelopeError(
            EnvelopeRejection.SCHEMA_MISMATCH, f"capability_id must be {CAPABILITY_ID}"
        )
    envelope = ObserverEnvelope(
        provider_id=_identity(record.get("provider_id"), name="provider_id") or "",
        observer_id=_identity(record.get("observer_id"), name="observer_id") or "",
        goal_id=_identity(record.get("goal_id"), name="goal_id") or "",
        session_id=_identity(record.get("session_id"), name="session_id") or "",
        agent_id=_identity(record.get("agent_id"), name="agent_id", optional=True),
        sequence=_sequence(record.get("sequence")),
        observed_at=_observed_at(record.get("observed_at")),
        clock=_clock(record.get("clock")),
        event_kind=_event_kind(record.get("event_kind")),
        summary=_summary(record.get("summary")),
        source_refs=_source_refs(record.get("source_refs")),
    )
    try:
        validate_public_safe_value(envelope.as_dict(), path="observer_envelope")
    except ValueError as exc:
        raise ObserverEnvelopeError(
            EnvelopeRejection.PUBLIC_SAFETY_VIOLATION, str(exc)
        ) from exc
    return envelope
