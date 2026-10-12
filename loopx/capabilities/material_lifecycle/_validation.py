"""Shared validation helpers for public-safe Material Lifecycle contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ...control_plane.runtime.public_safety import (
    REMOTE_LOCATION_SURFACE_PATTERN,
    find_public_safe_local_path,
)
from ...public_safe_text import CREDENTIAL_CATEGORIES
from ...public_safe_text import COMPACT_TOKEN_PATTERN as _TOKEN_RE
from ...public_safe_text import classify_private_text

# Refs #5136, direction 3: "does this text carry a local path?" is decided once
# by find_public_safe_local_path; this site keeps its own rejection message and
# length limit for whatever the owner recognizes.
#
# Refs #5136, direction 1: one categorized call decides the credential question.
# The alternation list this site kept beside SECRET_LIKE_SURFACE_PATTERN was a
# shape list rather than the "local threshold policy" its comment claimed, and it
# was byte-identical to the copy in decision_context/packets.py. The owner covers
# the one spelling that list reached and its category arms do not -- a credential
# label glued into a field name -- via ``include_compound_field_assignment``.
UNSAFE_FIELDS = {
    "api_key",
    "content",
    "credential",
    "credentials",
    "private_locator",
    "provider_payload",
    "raw_chat",
    "raw_content",
    "raw_provider_payload",
    "token",
    "tool_output",
}


def compact_text(value: Any, *, field: str, max_len: int = 320) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must be non-empty")
    if len(text) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    if find_public_safe_local_path(text) is not None:
        raise ValueError(f"{field} must not contain a local path")
    if REMOTE_LOCATION_SURFACE_PATTERN.search(text):
        raise ValueError(f"{field} must use an opaque reference, not a raw URL")
    if classify_private_text(
        text,
        categories=CREDENTIAL_CATEGORIES,
        include_compound_field_assignment=True,
    ):
        raise ValueError(f"{field} contains a credential-like value")
    return text


def compact_token(value: Any, *, field: str) -> str:
    token = compact_text(value, field=field, max_len=128)
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(
            f"{field} must contain only letters, digits, dot, colon, dash, or underscore"
        )
    return token


def iso_timestamp(value: Any, *, field: str) -> str:
    timestamp = compact_text(value, field=field, max_len=64)
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp


def nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def positive_int(value: Any, *, field: str) -> int:
    normalized = nonnegative_int(value, field=field)
    if normalized == 0:
        raise ValueError(f"{field} must be positive")
    return normalized


DEFAULT_MAX_MATERIALS_PER_ENTRY = 3


def material_catalog_entry_limit(
    *,
    explicit: int | None,
    catalog: Mapping[str, Any] | None,
) -> int:
    """Resolve the per-entry material cap from an explicit override or catalog.

    The catalog contract reads
    ``rankings.ranked_entries.constraints.max_materials_per_entry``. When a
    catalog is supplied but no explicit override is, that constraint is
    required so builders never silently fall back to a stale default.
    """
    if explicit is not None:
        return positive_int(explicit, field="max_materials_per_entry")
    if catalog is None:
        return DEFAULT_MAX_MATERIALS_PER_ENTRY
    try:
        constraint = catalog["rankings"]["ranked_entries"]["constraints"][
            "max_materials_per_entry"
        ]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "catalog must define rankings.ranked_entries.constraints."
            "max_materials_per_entry when max_materials_per_entry is not provided"
        ) from exc
    return positive_int(constraint, field="catalog.max_materials_per_entry")


def token_list(
    values: Sequence[Any] | None,
    *,
    field: str,
    max_items: int = 100,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of compact tokens")
    if len(values) > max_items:
        raise ValueError(f"{field} must contain at most {max_items} items")
    return sorted({compact_token(value, field=f"{field}[]") for value in values})


def check_record_keys(
    value: Mapping[str, Any],
    *,
    field: str,
    allowed: set[str],
    required: set[str],
) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    keys = {str(key) for key in value}
    unsafe = sorted(keys & UNSAFE_FIELDS)
    if unsafe:
        raise ValueError(f"{field} contains unsafe fields: {', '.join(unsafe)}")
    unexpected = sorted(keys - allowed)
    if unexpected:
        raise ValueError(
            f"{field} contains unsupported fields: {', '.join(unexpected)}"
        )
    missing = sorted(key for key in required if value.get(key) is None)
    if missing:
        raise ValueError(f"{field} is missing required fields: {', '.join(missing)}")


def packet_ref(prefix: str, packet: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            packet,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}-{digest}"


def capability_contract(*, packet_role: str, project_scoped: bool = False) -> dict[str, Any]:
    return {
        "capability_id": "material_lifecycle",
        "scope": "project" if project_scoped else "goal",
        "default_enabled": False,
        "packet_role": packet_role,
        "creates_authority": False,
        "mutates_core_state": False,
    }
