"""Interpret finite source period encodings and parent-declared return periods.

Source extraction, context identity, authenticity, pinned payloads, lifecycle
and knowledge clocks remain caller-owned. This module never repairs a source.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

FINANCE_PERIOD_ENCODING_SCHEMA_VERSION = "finance_period_encoding_assessment_v1"
FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION = "finance_period_comparison_input_v1"
FINANCE_PERIOD_COMPARISON_INPUT_V2_SCHEMA_VERSION = "finance_period_comparison_input_v2"
FINANCE_PERIOD_COMPARISON_SCHEMA_VERSION = "finance_period_comparison_assessment_v1"
_LITERAL = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})"
    r"(?:T(?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?))?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?\Z"
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROLE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")


class PeriodKind(StrEnum):
    UNKNOWN = "unknown"
    INSTANT = "instant"
    DURATION = "duration"


def _boundary(
    literal: str, element_role: str, *, explicit_time: bool = False
) -> dict[str, Any]:
    if not isinstance(literal, str) or len(literal) > 128:
        raise ValueError("period boundary requires a bounded date/dateTime string")
    match = _LITERAL.fullmatch(literal.strip())
    if not match or (explicit_time and not match["time"]):
        raise ValueError("unsupported period date/dateTime encoding")
    zone = match["zone"]
    tz = None
    if zone == "Z":
        tz = UTC
    elif zone:
        hour, minute = map(int, zone[1:].split(":"))
        if hour > 14 or minute > 59 or (hour == 14 and minute):
            raise ValueError("period timezone offset is outside the supported range")
        tz = timezone(
            timedelta(minutes=(hour * 60 + minute) * (1 if zone[0] == "+" else -1))
        )
    try:
        local = datetime.fromisoformat(
            match["date"] + "T" + (match["time"] or "00:00:00")
        )
        if not match["time"] and element_role in {"endDate", "instant"}:
            local += timedelta(days=1)
        absolute = (
            local.replace(tzinfo=tz).astimezone(UTC).isoformat().replace("+00:00", "Z")
            if tz
            else None
        )
    except (ValueError, OverflowError) as exc:
        raise ValueError(
            "period boundary is invalid or outside supported calendar years"
        ) from exc
    return dict(
        literal=literal,
        element_role=element_role,
        encoding="dateTime" if match["time"] else "date",
        local_boundary=local.isoformat(),
        timezone=zone,
        utc_boundary=absolute,
    )


def assess_period_encoding(context: Mapping[str, str] | None) -> dict[str, Any]:
    """Apply XBRL 2.1 §4.7.2 without inventing an absent timezone.

    This bounded API supports AD years 0001–9999 and at most six fractional
    second digits, not the complete XML Schema lexical space or DTS validation.
    None explicitly means no original context; stored dates cannot fill it.
    """
    kind = PeriodKind.UNKNOWN
    boundaries: dict[str, Any] = {}
    valid = None
    ordering = "unknown"
    if context is not None:
        if not isinstance(context, Mapping) or set(context) not in (
            {"instant"},
            {"startDate", "endDate"},
        ):
            raise ValueError(
                "finite context requires instant or exactly startDate and endDate"
            )
        boundaries = {k: _boundary(v, k) for k, v in context.items()}
        kind = PeriodKind.INSTANT if "instant" in context else PeriodKind.DURATION
        if kind == PeriodKind.DURATION:
            start, end = boundaries["startDate"], boundaries["endDate"]
            if start["utc_boundary"] and end["utc_boundary"]:
                ordering = "absolute"
                valid = datetime.fromisoformat(
                    end["utc_boundary"]
                ) > datetime.fromisoformat(start["utc_boundary"])
            elif not start["timezone"] and not end["timezone"]:
                ordering = "local_only"
                valid = datetime.fromisoformat(
                    end["local_boundary"]
                ) > datetime.fromisoformat(start["local_boundary"])
    return dict(
        schema_version=FINANCE_PERIOD_ENCODING_SCHEMA_VERSION,
        context=None if context is None else dict(context),
        kind=kind.value,
        boundaries=boundaries,
        duration_valid=valid,
        ordering_basis=ordering,
        absolute_boundaries_known=bool(boundaries)
        and all(b["utc_boundary"] for b in boundaries.values()),
        source_context_authenticated=False,
        economic_periods_assessed=False,
        trading_allowed=False,
    )


def _optional_ref(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(
            "period evidence reference must be a bounded nonempty string or null"
        )
    return True


def _digest(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("period source digest must be a SHA-256 reference or null")
    return True


def _operand(operand: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(operand, Mapping) or set(operand) != {
        "source_digest",
        "context",
        "economic_period",
    }:
        raise ValueError(
            "period operand requires source_digest, context and economic_period"
        )
    source_known = _digest(operand["source_digest"])
    encoded = assess_period_encoding(operand["context"])
    economic = operand["economic_period"]
    reasons: list[str] = []
    derived: dict[str, Any] = {}
    if encoded["kind"] != PeriodKind.DURATION:
        reasons.append("finite_duration_context_missing")
    elif encoded["duration_valid"] is not True:
        reasons.append("encoded_duration_nonpositive_or_unproven")
    if not encoded["absolute_boundaries_known"]:
        reasons.append("source_timezone_missing")
    if economic is None:
        reasons.append("economic_period_missing")
    else:
        required = {
            "context_source_digest",
            "evidence_ref",
            "role",
            "start",
            "end",
            "start_basis",
        }
        optional = {"event_instant", "event_mapping_evidence_ref"}
        if (
            not isinstance(economic, Mapping)
            or not required <= set(economic)
            or set(economic) - required - optional
        ):
            raise ValueError(
                "economic period has unsupported or missing declaration fields"
            )
        if not isinstance(economic["role"], str) or not _ROLE.fullmatch(
            economic["role"]
        ):
            raise ValueError(
                "economic period role must be a bounded exact source-parent token"
            )
        if not isinstance(economic["start_basis"], str) or economic[
            "start_basis"
        ] not in {"calendar", "event"}:
            raise ValueError("economic start_basis must be calendar or event")
        declaration_source_known = _digest(economic["context_source_digest"])
        if not source_known or not declaration_source_known:
            reasons.append("context_source_binding_missing")
        elif economic["context_source_digest"] != operand["source_digest"]:
            reasons.append("context_source_binding_mismatch")
        if not _optional_ref(economic["evidence_ref"]):
            reasons.append("economic_period_evidence_missing")
        for field, role in [("start", "startDate"), ("end", "endDate")]:
            if economic[field] is None:
                reasons.append("economic_boundary_missing")
            else:
                derived[field] = _boundary(economic[field], role, explicit_time=True)
                if not derived[field]["utc_boundary"]:
                    reasons.append("economic_timezone_missing")
        if all(derived.get(k, {}).get("utc_boundary") for k in ["start", "end"]):
            if datetime.fromisoformat(
                derived["end"]["utc_boundary"]
            ) <= datetime.fromisoformat(derived["start"]["utc_boundary"]):
                reasons.append("economic_duration_nonpositive")
            for name, role in [("start", "startDate"), ("end", "endDate")]:
                original = encoded["boundaries"].get(role, {}).get("utc_boundary")
                if original and original != derived[name]["utc_boundary"]:
                    reasons.append("economic_encoded_boundary_mismatch")
        event = economic.get("event_instant")
        event_proof = economic.get("event_mapping_evidence_ref")
        proof_present = _optional_ref(event_proof)
        if economic["start_basis"] == "event":
            if event is None or not proof_present:
                reasons.append("event_to_duration_mapping_missing")
            if event is not None:
                derived["event"] = _boundary(event, "instant", explicit_time=True)
                if not derived["event"]["utc_boundary"]:
                    reasons.append("event_timezone_missing")
                elif (
                    derived.get("start", {}).get("utc_boundary")
                    and derived["event"]["utc_boundary"]
                    != derived["start"]["utc_boundary"]
                ):
                    reasons.append("event_to_duration_boundary_mismatch")
        elif event is not None or event_proof is not None:
            raise ValueError("calendar start does not accept an event mapping")
    return dict(
        source_digest=operand["source_digest"],
        encoding=encoded,
        economic_period=None if economic is None else copy.deepcopy(dict(economic)),
        derived_economic_boundaries=derived,
        reason_codes=sorted(set(reasons)),
        declaration_eligible=not reasons,
        source_binding_assurance="caller_asserted_not_authenticated",
    )


def assess_period_comparison(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Check source-bound parent declarations; eligibility is only this axis.

    The caller must independently admit original source/parent evidence and
    match the current immutable payload/pin, subject, metric, unit, lifecycle,
    purpose and cutoffs. A checksum or evidence reference is not authentication.
    """
    if isinstance(payload, Mapping) and payload.get("schema_version") == FINANCE_PERIOD_COMPARISON_INPUT_V2_SCHEMA_VERSION:
        from .statement_basis import assess_statement_comparison

        return assess_statement_comparison(payload)
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "left",
        "right",
        "period_intent",
    }:
        raise ValueError(
            "period comparison requires schema_version, left, right and period_intent"
        )
    if payload["schema_version"] != FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported Finance period comparison input version")
    intent = payload["period_intent"]
    if not isinstance(intent, str) or intent not in {"same_period", "cross_period"}:
        raise ValueError("period_intent must be same_period or cross_period")
    left, right = _operand(payload["left"]), _operand(payload["right"])
    reasons = sorted(set(left["reason_codes"] + right["reason_codes"]))
    relation = "unproven"
    if (
        left["economic_period"] is not None
        and right["economic_period"] is not None
        and left["economic_period"]["role"] != right["economic_period"]["role"]
    ):
        reasons.append("economic_role_mismatch")
    if left["declaration_eligible"] and right["declaration_eligible"]:
        if left["economic_period"]["role"] == right["economic_period"]["role"]:
            same = all(
                left["derived_economic_boundaries"][k]["utc_boundary"]
                == right["derived_economic_boundaries"][k]["utc_boundary"]
                for k in ["start", "end"]
            )
            relation = "same" if same else "distinct"
            if same != (intent == "same_period"):
                reasons.append("economic_period_intent_mismatch")
    return dict(
        schema_version=FINANCE_PERIOD_COMPARISON_SCHEMA_VERSION,
        period_intent=intent,
        operands={"left": left, "right": right},
        economic_period_relation=relation,
        economic_declarations_assessed=True,
        period_evidence_eligible=not reasons,
        reason_codes=sorted(set(reasons)),
        source_evidence_authenticated=False,
        source_lifecycle_assessed=False,
        financial_admission=False,
        trading_allowed=False,
    )
