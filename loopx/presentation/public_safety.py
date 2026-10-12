from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..public_safe_text import (
    PRESENTATION_LOCAL_PATH_PATTERNS as LOCAL_PATH_PATTERNS,
    PRESENTATION_PUBLIC_BOUNDARY_PATTERNS as PUBLIC_BOUNDARY_PATTERNS,
)


PUBLIC_SAFE_BOUNDARY_FIELDS = (
    "raw_logs_recorded",
    "raw_transcripts_recorded",
    "raw_connector_payloads_recorded",
    "credential_values_recorded",
    "absolute_paths_recorded",
    "private_source_bodies_recorded",
)

def public_safe_boundary() -> dict[str, bool]:
    return {field: False for field in PUBLIC_SAFE_BOUNDARY_FIELDS}


def redact_public_text(
    value: Any,
    *,
    limit: int,
    replacements: Mapping[str, str] | None = None,
    truncation_marker: str = "...",
) -> str:
    text = str(value or "").strip()
    for source, target in (replacements or {}).items():
        text = text.replace(source, target)
    for pattern in LOCAL_PATH_PATTERNS:
        text = pattern.sub("<local-path-redacted>", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + truncation_marker
    return text


def scan_public_boundary_text(text: str) -> dict[str, object]:
    """Scan text for obvious private material before public presentation.

    Returns ``{"ok": bool, "warnings": [label, ...]}``. The scanner is the
    canonical public/private boundary check for generated presentation text;
    the static-site exporter and the session dash generator share it.
    """

    warnings = [
        label
        for label, pattern in PUBLIC_BOUNDARY_PATTERNS
        if pattern.search(text)
    ]
    return {"ok": not warnings, "warnings": warnings}
