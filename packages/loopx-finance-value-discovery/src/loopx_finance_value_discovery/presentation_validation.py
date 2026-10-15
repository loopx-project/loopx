"""Finance-owned validation primitives for the decision-research view.

Core owns the provider-neutral presentation envelope and its public validator
registration hook. Finance owns the schema below, including the public-safe
text and evidence constraints that make its view safe to publish.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import math
import re
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse

_find_public_safe_local_path: Callable[[str | None], re.Pattern[str] | None] | None
try:
    from loopx.extensions.presentation import (
        find_public_safe_local_path as _find_public_safe_local_path,
    )
except (ImportError, ModuleNotFoundError):
    # Non-presentation Finance operations still support older Core versions.
    _find_public_safe_local_path = None


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ANCHOR_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MARKUP_RE = re.compile(r"<[^>]*>|javascript:", re.IGNORECASE)
_LEGACY_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s(])(?:~[/\\]|/+(?:Users|home|tmp|private|var|etc|opt)/|"
    r"[A-Za-z]:[\\/])"
)
_LEGACY_MULTISLASH_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s:=('])/{2,}(?:Users|home|tmp|private|var|etc|opt)/",
    re.IGNORECASE,
)
_PRIVATE_RELATIVE_PATH_RE = re.compile(
    r"(?:^|[\s:=('/\\])\.(?:codex|git|local)(?:[/\\]|$)",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:api[_ -]?key|access[_ -]?token|secret|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:account[\s_-]*(?:id|number)|order[\s_-]*(?:id|request)|"
    r"portfolio(?:[\s_-]+holdings)?|position[\s_-]*size)\b",
    re.IGNORECASE,
)
_FORBIDDEN_KEY_TOKENS = {
    "account_id", "account_number", "access_token", "api_key", "credential",
    "cookie", "order_id", "order_request", "portfolio", "portfolio_holdings",
    "position_size", "raw_provider", "raw_request", "raw_response", "secret", "token",
}


def contains_local_path(value: str) -> bool:
    """Apply the shared public-path classifier and retain Finance's legacy edge."""

    if _find_public_safe_local_path is None:
        return bool(_LEGACY_LOCAL_PATH_RE.search(value))
    return bool(
        _find_public_safe_local_path(value)
        or _LEGACY_MULTISLASH_LOCAL_PATH_RE.search(value)
    )


def _is_forbidden_key_name(value: Any) -> bool:
    normalized = str(value).lower().replace("-", "_")
    return any(
        normalized == token
        or normalized.startswith(f"{token}_")
        or normalized.endswith(f"_{token}")
        for token in _FORBIDDEN_KEY_TOKENS
    )


def record(value: Any, *, context: str, allowed: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    unsupported = sorted(set(value) - allowed)
    if unsupported:
        raise ValueError(f"{context} contains unsupported keys {unsupported}")
    return value


def plain_text(value: Any, *, context: str, max_length: int = 600, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be plain text")
    text = value.strip()
    if not text and not allow_empty:
        raise ValueError(f"{context} must be non-empty plain text")
    if len(text) > max_length:
        raise ValueError(f"{context} must contain at most {max_length} characters")
    if "\x00" in text or _MARKUP_RE.search(text):
        raise ValueError(f"{context} must be plain text without markup")
    if contains_local_path(text) or _PRIVATE_RELATIVE_PATH_RE.search(text):
        raise ValueError(f"{context} must not contain a local path")
    if _CREDENTIAL_RE.search(text) or _SENSITIVE_TEXT_RE.search(text):
        raise ValueError(f"{context} must not contain sensitive material")
    return text


def required_text(record_value: Mapping[str, Any], key: str, *, context: str, max_length: int = 600) -> str:
    return plain_text(record_value.get(key), context=f"{context}.{key}", max_length=max_length)


def identifier(value: Any, *, context: str) -> str:
    text = plain_text(value, context=context, max_length=128)
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{context} must be a stable identifier")
    return text


def enum(value: Any, allowed: set[str], *, context: str) -> str:
    text = plain_text(value, context=context, max_length=64)
    if text not in allowed:
        raise ValueError(f"{context} must be one of {sorted(allowed)}")
    return text


def boolean(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{context} must be finite")
    return result


def iso_value(value: Any, *, context: str, date_only_allowed: bool = True) -> str:
    text = plain_text(value, context=context, max_length=40)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        if "T" in normalized:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                raise ValueError
        elif date_only_allowed:
            date.fromisoformat(normalized)
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid ISO-8601 value") from exc
    return text


def bounded_list(value: Any, *, context: str, maximum: int, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    if len(value) < minimum:
        raise ValueError(f"{context} requires at least {minimum} items")
    if len(value) > maximum:
        raise ValueError(f"{context} must contain at most {maximum} items")
    return value


def text_list(value: Any, *, context: str, maximum: int, minimum: int = 0, item_limit: int = 600) -> list[str]:
    return [
        plain_text(item, context=f"{context}[{index}]", max_length=item_limit)
        for index, item in enumerate(bounded_list(value, context=context, minimum=minimum, maximum=maximum))
    ]


def assert_allowed_keys_recursively(value: Any, *, context: str = "view") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if _is_forbidden_key_name(normalized) and normalized not in {
                "raw_provider_payload_recorded", "private_source_content_read",
            }:
                raise ValueError(f"{context} contains forbidden key `{key}`")
            assert_allowed_keys_recursively(child, context=f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_allowed_keys_recursively(child, context=f"{context}[{index}]")
    elif isinstance(value, str):
        plain_text(value, context=context, max_length=2_000, allow_empty=True)


def evidence_reference(value: Any, *, context: str) -> str:
    reference = plain_text(value, context=context, max_length=500)
    if "://" not in reference:
        if not _ID_RE.fullmatch(reference):
            raise ValueError(f"{context} must be a compact evidence reference")
        return reference
    parsed = urlparse(reference)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https" or not hostname or hostname in {"localhost", "127.0.0.1", "::1"}
        or hostname.endswith((".internal", ".local")) or hostname.startswith(("private.", "internal."))
        or parsed.username or parsed.password
    ):
        raise ValueError(f"{context} contains an unsafe evidence reference")
    fields = {
        key.lower().replace("-", "_")
        for component in (parsed.query, parsed.fragment)
        for key, _value in parse_qsl(component, keep_blank_values=True)
    }
    if any(_is_forbidden_key_name(field) for field in fields):
        raise ValueError(f"{context} contains an unsafe evidence reference")
    return reference


def public_reference_tree(value: Any, context: str = "input") -> None:
    """Keep embedded references under the existing publication URL boundary."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{context}.{key}"
            if child is not None and (key == "ref" or key.endswith("_ref")):
                evidence_reference(child, context=path)
            else:
                public_reference_tree(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            public_reference_tree(child, f"{context}[{index}]")
