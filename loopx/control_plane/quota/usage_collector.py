"""Provider-neutral usage capture and ingest for per-goal token/cost/duration.

This module owns both sides of the seam described in
``docs/architecture/rfcs/goal-usage-token-cost-v0.md`` and GH-C95 / #3163:

- **Producer** (``build_compact_usage_row`` / ``ingest_usage_into_run_record``):
  emit a versioned, provider-neutral compact ``run_usage_v0`` row into existing
  run-history records. Cumulative host snapshots become non-negative deltas at
  this boundary and bind to ``source_snapshot_id`` so replay is idempotent.
- **Reader** (``collect_usage_for_run``): consume only that typed row for the
  aggregate loop in ``usage_summary``. The ledger (run history + this seam)
  remains the single source of truth; there is no second usage store.

Missing measurements stay omitted (unknown), never coerced to zero.
Malformed, negative, non-finite, reset, or out-of-order observations fail
closed with a typed error. Prompts, completions, tool output, credentials, provider payloads,
and anything that reconstructs a conversation are never captured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, MutableMapping, TypedDict


RUN_USAGE_SCHEMA_VERSION = "run_usage_v0"
_MEASUREMENT_KINDS = frozenset({"absolute", "delta"})
_REQUIRED_TOKEN_FIELDS: tuple[Literal["input_tokens", "output_tokens"], ...] = ("input_tokens", "output_tokens")
_OPTIONAL_INT_FIELDS: tuple[Literal["cache_tokens", "duration_ms"], ...] = ("cache_tokens", "duration_ms")
_OPTIONAL_FLOAT_FIELDS: tuple[Literal["cost_usd"], ...] = ("cost_usd",)
_LABEL_FIELDS = ("provider", "model")


class _SnapshotCounters(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    cache_tokens: int | None
    cost_usd: float | None
    duration_ms: int | None


class UsageRowError(ValueError):
    """Fail-closed diagnostic for malformed or illegal usage observations."""


@dataclass(frozen=True)
class UsageSample:
    """Normalized, public-safe per-run LLM usage.

    Token counts are always present once a typed row is accepted. Optional
    aggregates stay ``None`` when the producer did not measure them so callers
    can treat absence as unknown rather than zero.
    """

    input_tokens: int
    output_tokens: int
    cache_tokens: int | None
    cost_usd: float | None
    duration_ms: int | None
    provider: str
    model: str
    source_snapshot_id: str
    measurement_kind: str


def _require_non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageRowError(f"usage.{field} must be a non-negative number")
    if isinstance(value, float) and not math.isfinite(value):
        raise UsageRowError(f"usage.{field} must be finite")
    if float(value) != int(value):
        raise UsageRowError(f"usage.{field} must be a whole number")
    coerced = int(value)
    if coerced < 0:
        raise UsageRowError(f"usage.{field} must be non-negative")
    return coerced


def _optional_non_negative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _require_non_negative_int(value, field=field)


def _optional_non_negative_float(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageRowError(f"usage.{field} must be a non-negative number")
    coerced = float(value)
    if not math.isfinite(coerced):
        raise UsageRowError(f"usage.{field} must be finite")
    if coerced < 0:
        raise UsageRowError(f"usage.{field} must be non-negative")
    return coerced


def _require_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise UsageRowError(f"usage.{field} is required")
    if len(text) > 120:
        raise UsageRowError(f"usage.{field} exceeds 120 characters")
    return text


def _require_snapshot_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise UsageRowError("usage.source_snapshot_id is required")
    if len(text) > 240:
        raise UsageRowError("usage.source_snapshot_id exceeds 240 characters")
    return text


def _snapshot_counters(snapshot: Mapping[str, Any], *, label: str) -> _SnapshotCounters:
    if not isinstance(snapshot, Mapping):
        raise UsageRowError(f"{label} must be a mapping")
    return {
        "input_tokens": _require_non_negative_int(
            snapshot.get("input_tokens"), field=f"{label}.input_tokens"
        ),
        "output_tokens": _require_non_negative_int(
            snapshot.get("output_tokens"), field=f"{label}.output_tokens"
        ),
        "cache_tokens": _optional_non_negative_int(
            snapshot.get("cache_tokens"), field=f"{label}.cache_tokens"
        ),
        "cost_usd": _optional_non_negative_float(
            snapshot.get("cost_usd"), field=f"{label}.cost_usd"
        ),
        "duration_ms": _optional_non_negative_int(
            snapshot.get("duration_ms"), field=f"{label}.duration_ms"
        ),
    }


def _delta_optional_int(
    current: int | None,
    previous: int | None,
    *,
    field: str,
) -> int | None:
    if current is None:
        return None
    if previous is None:
        return current
    delta = current - previous
    if delta < 0:
        raise UsageRowError(
            f"usage.{field} reset or out-of-order cumulative observation"
        )
    return delta


def _delta_optional_float(
    current: float | None,
    previous: float | None,
    *,
    field: str,
) -> float | None:
    if current is None:
        return None
    if previous is None:
        return current
    delta = current - previous
    if delta < 0:
        raise UsageRowError(
            f"usage.{field} reset or out-of-order cumulative observation"
        )
    return delta


def build_compact_usage_row(
    *,
    input_tokens: int,
    output_tokens: int,
    source_snapshot_id: str,
    provider: str,
    model: str,
    cache_tokens: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    measurement_kind: str = "absolute",
    previous_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a versioned compact usage row for run history.

    When ``previous_snapshot`` is supplied, counters are converted to a
    non-negative delta against that cumulative basis. Replaying the same
    ``source_snapshot_id`` as the previous snapshot returns the prior absolute
    zero-delta row without inventing new spend (idempotent replay).
    """
    kind = str(measurement_kind or "").strip()
    if kind not in _MEASUREMENT_KINDS:
        raise UsageRowError("usage.measurement_kind must be absolute or delta")

    snapshot_id = _require_snapshot_id(source_snapshot_id)
    provider_label = _require_label(provider, field="provider")
    model_label = _require_label(model, field="model")

    current: _SnapshotCounters = {
        "input_tokens": _require_non_negative_int(input_tokens, field="input_tokens"),
        "output_tokens": _require_non_negative_int(output_tokens, field="output_tokens"),
        "cache_tokens": _optional_non_negative_int(cache_tokens, field="cache_tokens"),
        "cost_usd": _optional_non_negative_float(cost_usd, field="cost_usd"),
        "duration_ms": _optional_non_negative_int(duration_ms, field="duration_ms"),
    }

    if previous_snapshot is not None:
        previous_id = str(previous_snapshot.get("source_snapshot_id") or "").strip()
        if previous_id and previous_id == snapshot_id:
            # Same snapshot identity is only an idempotent replay when the full
            # cumulative observation (counters and binding labels) is unchanged.
            # A reused, corrupt, or out-of-order identity carrying different
            # numbers must fail closed instead of silently zeroing real usage.
            prior = _snapshot_counters(previous_snapshot, label="previous_snapshot")
            mismatched: list[str] = sorted(
                field
                for field in (*_REQUIRED_TOKEN_FIELDS, *_OPTIONAL_INT_FIELDS, *_OPTIONAL_FLOAT_FIELDS)
                if current[field] != prior[field]
            )
            prior_labels = {
                field: str(previous_snapshot.get(field) or "").strip()
                for field in _LABEL_FIELDS
            }
            if prior_labels["provider"] != provider_label:
                mismatched.append("provider")
            if prior_labels["model"] != model_label:
                mismatched.append("model")
            if mismatched:
                raise UsageRowError(
                    "usage.source_snapshot_id replayed with a different cumulative "
                    "observation (" + ", ".join(mismatched) + ")"
                )
            kind = "delta"
            current = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_tokens": 0 if previous_snapshot.get("cache_tokens") is not None else None,
                "cost_usd": 0.0 if previous_snapshot.get("cost_usd") is not None else None,
                "duration_ms": 0 if previous_snapshot.get("duration_ms") is not None else None,
            }
        else:
            prior = _snapshot_counters(previous_snapshot, label="previous_snapshot")
            kind = "delta"
            current = {
                "input_tokens": _delta_optional_int(
                    current["input_tokens"],
                    prior["input_tokens"],
                    field="input_tokens",
                ),
                "output_tokens": _delta_optional_int(
                    current["output_tokens"],
                    prior["output_tokens"],
                    field="output_tokens",
                ),
                "cache_tokens": _delta_optional_int(
                    current["cache_tokens"],
                    prior["cache_tokens"],
                    field="cache_tokens",
                ),
                "cost_usd": _delta_optional_float(
                    current["cost_usd"],
                    prior["cost_usd"],
                    field="cost_usd",
                ),
                "duration_ms": _delta_optional_int(
                    current["duration_ms"],
                    prior["duration_ms"],
                    field="duration_ms",
                ),
            }

    if current["input_tokens"] is None or current["output_tokens"] is None:
        raise UsageRowError("usage.input_tokens and usage.output_tokens are required")

    row: dict[str, Any] = {
        "schema_version": RUN_USAGE_SCHEMA_VERSION,
        "measurement_kind": kind,
        "source_snapshot_id": snapshot_id,
        "input_tokens": current["input_tokens"],
        "output_tokens": current["output_tokens"],
        "provider": provider_label,
        "model": model_label,
    }
    if current["cache_tokens"] is not None:
        row["cache_tokens"] = current["cache_tokens"]
    if current["cost_usd"] is not None:
        row["cost_usd"] = current["cost_usd"]
    if current["duration_ms"] is not None:
        row["duration_ms"] = current["duration_ms"]
    return row


def normalize_compact_usage_row(usage: Any) -> dict[str, Any]:
    """Validate a compact usage mapping and return a normalized ``run_usage_v0`` row."""
    if not isinstance(usage, Mapping):
        raise UsageRowError("usage must be a mapping")
    if str(usage.get("schema_version") or "") != RUN_USAGE_SCHEMA_VERSION:
        raise UsageRowError(
            f"usage.schema_version must be {RUN_USAGE_SCHEMA_VERSION}"
        )
    return build_compact_usage_row(
        input_tokens=usage.get("input_tokens"),  # type: ignore[arg-type]
        output_tokens=usage.get("output_tokens"),  # type: ignore[arg-type]
        cache_tokens=usage.get("cache_tokens"),
        cost_usd=usage.get("cost_usd"),
        duration_ms=usage.get("duration_ms"),
        provider=str(usage.get("provider") or ""),
        model=str(usage.get("model") or ""),
        source_snapshot_id=str(usage.get("source_snapshot_id") or ""),
        measurement_kind=str(usage.get("measurement_kind") or "absolute"),
    )


def ingest_usage_into_run_record(
    record: MutableMapping[str, Any],
    measurement: Mapping[str, Any] | None = None,
    *,
    previous_snapshot: Mapping[str, Any] | None = None,
    index_record: MutableMapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Producer call site: attach a typed usage row onto a run-history record.

    ``measurement`` may be a finished ``run_usage_v0`` row or raw absolute
    counters plus labels. When omitted, any existing ``record["usage"]`` is
    normalized in place. Returns the attached row, or ``None`` when neither the
    record nor the caller supplied usage.
    """
    raw = measurement if measurement is not None else record.get("usage")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise UsageRowError("usage must be a mapping")

    if str(raw.get("schema_version") or "") == RUN_USAGE_SCHEMA_VERSION and previous_snapshot is None:
        row = normalize_compact_usage_row(raw)
    else:
        row = build_compact_usage_row(
            input_tokens=raw.get("input_tokens"),  # type: ignore[arg-type]
            output_tokens=raw.get("output_tokens"),  # type: ignore[arg-type]
            cache_tokens=raw.get("cache_tokens"),
            cost_usd=raw.get("cost_usd"),
            duration_ms=raw.get("duration_ms"),
            provider=str(raw.get("provider") or ""),
            model=str(raw.get("model") or ""),
            source_snapshot_id=str(
                raw.get("source_snapshot_id")
                or record.get("run_id")
                or record.get("generated_at")
                or ""
            ),
            measurement_kind=str(raw.get("measurement_kind") or "absolute"),
            previous_snapshot=previous_snapshot,
        )
    record["usage"] = row
    if index_record is not None:
        index_record["usage"] = dict(row)
    return row


def collect_usage_for_run(run: dict[str, Any]) -> UsageSample | None:
    """Return normalized usage for a run, or ``None`` if the run reports none.

    Absent usage contributes nothing. A present but illegal usage block fails
    closed instead of clamping, zero-filling, or silently dropping fields.
    """
    usage = run.get("usage")
    if usage is None:
        return None
    row = normalize_compact_usage_row(usage)
    return UsageSample(
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cache_tokens=row.get("cache_tokens"),
        cost_usd=row.get("cost_usd"),
        duration_ms=row.get("duration_ms"),
        provider=str(row["provider"]),
        model=str(row["model"]),
        source_snapshot_id=str(row["source_snapshot_id"]),
        measurement_kind=str(row["measurement_kind"]),
    )
