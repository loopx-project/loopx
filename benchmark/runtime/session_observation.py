"""Content-free, bounded observations from a live Codex session rollout.

This is an operational signal for liveness and capacity control.  Exact usage
accounting still uses the complete streaming reader owned by
``loopx.control_plane.quota.codex_session_usage``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SESSION_TAIL_BYTES = 16 * 1024 * 1024
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_COUNTED_RESPONSE_TYPES = frozenset(
    {
        "command_execution",
        "custom_tool_call",
        "function_call",
        "function_call_output",
        "local_shell_call",
        "message",
        "reasoning",
        "web_search_call",
    }
)


class CodexSessionObservationError(RuntimeError):
    """The selected live-rollout snapshot could not be observed safely."""


def _complete_tail(path: Path, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            start = max(0, size - max_bytes)
            # Inspect the preceding byte so an exact record boundary does not
            # discard an otherwise complete first record.
            stream.seek(max(0, start - 1))
            raw = stream.read(size - max(0, start - 1))
    except OSError as exc:
        raise CodexSessionObservationError(
            f"cannot read Codex session rollout tail: {type(exc).__name__}"
        ) from exc
    expected = size - max(0, start - 1)
    if len(raw) != expected:
        raise CodexSessionObservationError(
            "Codex session rollout changed while its tail was read"
        )

    leading_discarded = False
    data = raw
    if start:
        starts_at_boundary = raw[:1] == b"\n"
        data = raw[1:]
        if not starts_at_boundary:
            leading_discarded = True
            _partial, separator, data = data.partition(b"\n")
            if not separator:
                data = b""

    trailing_discarded = bool(data and not data.endswith(b"\n"))
    if trailing_discarded:
        prefix, separator, _partial = data.rpartition(b"\n")
        data = prefix + separator if separator else b""

    return data, {
        "source_size_bytes": size,
        "sampled_bytes": min(size, max_bytes),
        "tail_truncated": bool(start),
        "leading_record_discarded": leading_discarded,
        "trailing_record_discarded": trailing_discarded,
    }


def _compact_usage(value: Any) -> dict[str, int | float] | None:
    if not isinstance(value, Mapping):
        return None
    compact = {
        field: item
        for field in _USAGE_FIELDS
        if isinstance((item := value.get(field)), (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        and item >= 0
    }
    return compact or None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return value


def iter_codex_session_tail_records(
    rollout_path: Path,
    *,
    max_bytes: int = DEFAULT_SESSION_TAIL_BYTES,
) -> Iterator[Mapping[str, Any]]:
    """Yield valid objects from complete records in one bounded rollout tail."""
    data, _framing = _complete_tail(Path(rollout_path), max_bytes)
    for raw in data.splitlines():
        if not raw.strip():
            continue
        try:
            item = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(item, Mapping):
            yield item


def observe_codex_session_tail(
    rollout_path: Path,
    *,
    max_bytes: int = DEFAULT_SESSION_TAIL_BYTES,
) -> dict[str, Any]:
    """Observe only complete records from a bounded live-rollout tail.

    The result intentionally contains no prompt, completion, tool output, error
    message, local path, session id, or model id.  It is suitable for a frequent
    benchmark-control poll, but not for billing, exact trajectory conversion, or
    integrity qualification.
    """
    data, framing = _complete_tail(Path(rollout_path), max_bytes)
    counts: Counter[str] = Counter()
    latest_usage: dict[str, int | float] | None = None
    last_event_at: str | None = None
    complete_records = 0
    invalid_records = 0

    for raw in data.splitlines():
        if not raw.strip():
            continue
        complete_records += 1
        try:
            item = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid_records += 1
            continue
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        raw_event_type = payload.get("type")
        event_type = raw_event_type if isinstance(raw_event_type, str) else ""
        timestamp = _timestamp(item.get("timestamp"))
        if timestamp is not None and (
            last_event_at is None or timestamp > last_event_at
        ):
            last_event_at = timestamp
        if item.get("type") == "response_item" and event_type:
            counts[
                event_type if event_type in _COUNTED_RESPONSE_TYPES else "other"
            ] += 1
        if event_type == "token_count":
            info = payload.get("info")
            info = info if isinstance(info, dict) else {}
            observed = _compact_usage(info.get("total_token_usage"))
            if observed is not None:
                latest_usage = observed
    return {
        "schema_version": "codex_session_tail_observation_v0",
        **framing,
        "event_counts_scope": "bounded_tail",
        "event_counts": dict(counts),
        "complete_record_count": complete_records,
        "invalid_complete_record_count": invalid_records,
        "usage_cumulative": latest_usage,
        "last_event_at": last_event_at,
    }


def main(argv: list[str] | None = None) -> int:
    """Print one bounded observation for an explicitly selected rollout."""
    parser = argparse.ArgumentParser(
        description="Read a content-free bounded tail from one live Codex rollout."
    )
    parser.add_argument("rollout", type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_SESSION_TAIL_BYTES)
    options = parser.parse_args(argv)
    try:
        observation = observe_codex_session_tail(
            options.rollout, max_bytes=options.max_bytes
        )
    except (CodexSessionObservationError, ValueError) as exc:
        parser.exit(2, f"session observation unavailable: {type(exc).__name__}\n")
    print(
        json.dumps(
            observation,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
