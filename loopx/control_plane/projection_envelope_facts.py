"""Python facts adapter for the TypeScript-owned projection envelope.

Python-owned projections record when each source was read and what scope they
covered; `projection_envelope.ts` alone decides freshness, alerts and
completeness. The adapter exits when the projection itself migrates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .effect_runtime import effect_runtime_result
from .runtime.time import now_utc_iso

PROJECTION_ENVELOPE_SCHEMA_VERSION = "loopx_projection_envelope_v0"
SEAL_REQUEST_SCHEMA_VERSION = "loopx_projection_envelope_seal_request_v0"
SERVE_REQUEST_SCHEMA_VERSION = "loopx_projection_envelope_serve_request_v0"
ALERT_MARKER = "🔴"


def source_fact(
    source_id: str,
    *,
    read_status: str = "read",
    last_read_at: str | None = None,
    required: bool = True,
    source_updated_at: str | None = None,
    item_count: int | None = None,
    missing_count: int = 0,
    unreadable_count: int = 0,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "read_status": read_status,
        "last_read_at": last_read_at,
        "required": required,
        "source_updated_at": source_updated_at,
        "item_count": item_count,
        "missing_count": missing_count,
        "unreadable_count": unreadable_count,
    }


def seal_projection_envelope(
    *,
    projection: str,
    observed_at: str,
    sources: Sequence[Mapping[str, Any]],
    coverage: Mapping[str, Any],
    upstream: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return effect_runtime_result(
        "projection.envelope.seal",
        {
            "schema_version": SEAL_REQUEST_SCHEMA_VERSION,
            "projection": projection,
            "observed_at": observed_at,
            "sources": [dict(row) for row in sources],
            "coverage": dict(coverage),
            "upstream": [dict(row) for row in upstream],
        },
    )


def serve_projection_envelope(
    envelope: Mapping[str, Any], *, served_at: str | None = None
) -> dict[str, Any]:
    """Re-serve a stored envelope; staleness is recomputed, observed_at kept."""

    return effect_runtime_result(
        "projection.envelope.seal",
        {
            "schema_version": SERVE_REQUEST_SCHEMA_VERSION,
            "envelope": dict(envelope),
            "served_at": served_at or now_utc_iso(),
        },
    )


def render_projection_envelope_markdown(envelope: Any) -> list[str]:
    if not isinstance(envelope, Mapping):
        return [f"- projection: {ALERT_MARKER} no projection envelope; freshness and coverage unknown"]
    coverage = envelope.get("coverage") if isinstance(envelope.get("coverage"), Mapping) else {}
    sources = [row for row in envelope.get("sources") or [] if isinstance(row, Mapping)]
    absent_optional = sum(
        1 for row in sources if row.get("required") is False and row.get("read_status") != "read"
    )
    fresh_count = sum(1 for row in sources if row.get("status") == "fresh")
    marker = f"{ALERT_MARKER} " if envelope.get("alert") else ""
    line = (
        f"- projection: {marker}observed_at=`{envelope.get('observed_at')}` "
        f"age=`{envelope.get('age_seconds')}s`"
        + (" (cached)" if envelope.get("served_from_cache") else "")
        + f" sources_fresh=`{fresh_count}/{len(sources) - absent_optional}`"
        + (f" (+{absent_optional} optional absent)" if absent_optional else "")
        + f" coverage=`{coverage.get('included_count')}/{coverage.get('expected_count')}` "
        f"scope=`{coverage.get('scope')}`"
    )
    if coverage.get("truncated"):
        line += f" shown=`{coverage.get('shown_count')}/{coverage.get('available_count')}`"
    lines = [line]
    if envelope.get("alert"):
        alerts = ",".join(envelope.get("alert_reasons") or [])
        ids = ",".join(envelope.get("alert_source_ids") or []) or "-"
        omitted = ",".join(
            f"{row.get('reason')}={row.get('count')}"
            for row in coverage.get("omitted") or []
            if isinstance(row, Mapping)
        ) or "-"
        lines.append(
            f"- {ALERT_MARKER} projection alerts: `{alerts}` sources=`{ids}` omitted=`{omitted}`; "
            "state this before treating the projection as current or whole"
        )
    return lines
