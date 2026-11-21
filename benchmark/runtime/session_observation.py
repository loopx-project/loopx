"""Content-free, bounded observations from a live Codex session rollout.

This is an operational signal for liveness and capacity control.
``usage_cumulative`` retains the native ``token_count`` basis, which may exclude
compaction. Goal usage collection uses the complete streaming reader in
``loopx.control_plane.quota.codex_session_usage`` with that same counter basis.
For recorded request/compaction coverage, use the benchmark-toolkit trajectory
capture's ``request_coverage`` projection. Session counters and reconciled
all-request totals are separate observations, not additive charges.
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


def collect_context_policy_telemetry(
    runtime_root: Path | None = None,
    *,
    goal_id: str | None = None,
    context_dir: Path | None = None,
    usage: dict | None = None,
) -> dict:
    """Collect {KeepRaw, FoldToReceipt, EpochReset}, prefetch, quiet-turn, and cold/warm KV telemetry."""
    operator_counts = {"KeepRaw": 0, "FoldToReceipt": 0, "EpochReset": 0}
    quiet_turn_skips = 0
    prefetch_latency_ms = 0
    prefetch_bytes = 0
    prefetch_bytes_saved = 0
    round_trips_saved = 0
    seen_receipts: set[str] = set()

    def _consume_prefetch(entry: object) -> None:
        nonlocal prefetch_latency_ms, prefetch_bytes, prefetch_bytes_saved, round_trips_saved
        if not isinstance(entry, dict):
            return
        receipt_id = entry.get("receipt_sha256")
        if isinstance(receipt_id, str) and receipt_id:
            if receipt_id in seen_receipts:
                return
            seen_receipts.add(receipt_id)
        op = entry.get("operator")
        if op in operator_counts:
            operator_counts[op] += 1
        if isinstance(entry.get("latency_ms"), int) and entry["latency_ms"] >= 0:
            prefetch_latency_ms += entry["latency_ms"]
        if isinstance(entry.get("retained_bytes"), int) and entry["retained_bytes"] >= 0:
            prefetch_bytes += entry["retained_bytes"]
        if isinstance(entry.get("bytes_saved"), int) and entry["bytes_saved"] >= 0:
            prefetch_bytes_saved += entry["bytes_saved"]
        if isinstance(entry.get("round_trips_saved"), int) and entry["round_trips_saved"] >= 0:
            round_trips_saved += entry["round_trips_saved"]

    def _consume_quiet(entry: object) -> None:
        nonlocal quiet_turn_skips
        if isinstance(entry, dict) and entry.get("skipped") is True:
            quiet_turn_skips += 1

    if runtime_root is not None and goal_id:
        turns_dir = Path(runtime_root) / "goals" / goal_id / "turns"
        if turns_dir.is_dir():
            for path in sorted(turns_dir.glob("*.json")):
                try:
                    journal = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(journal, dict):
                    _consume_prefetch(journal.get("epoch_prefetch"))
                    _consume_quiet(journal.get("quiet_turn"))

    if context_dir is not None:
        session_file = Path(context_dir) / "session.json"
        if session_file.is_file():
            try:
                session_data = json.loads(session_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                session_data = {}
            if isinstance(session_data, dict):
                _consume_prefetch(session_data.get("epoch_prefetch"))
                _consume_quiet(session_data.get("quiet_turn"))

    cold = {"requests": 0, "uncached_prefix_kv_tokens": 0, "cached_prefix_kv_tokens": 0}
    warm = {"requests": 0, "uncached_prefix_kv_tokens": 0, "cached_prefix_kv_tokens": 0}
    if isinstance(usage, dict):
        coverage = usage.get("request_coverage")
        requests = coverage.get("requests") if isinstance(coverage, dict) else None
        if isinstance(requests, list):
            for req in requests:
                if not isinstance(req, dict):
                    continue
                inp = req.get("input_tokens")
                cached = req.get("cached_input_tokens")
                if not isinstance(inp, int) or not isinstance(cached, int) or inp < cached:
                    continue
                uncached = inp - cached
                bucket = cold if (cached == 0 or req.get("cache_regime") == "cold") else warm
                bucket["requests"] += 1
                bucket["uncached_prefix_kv_tokens"] += uncached
                bucket["cached_prefix_kv_tokens"] += cached
        elif isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("cached_input_tokens"), int):
            cached = usage["cached_input_tokens"]
            uncached = max(0, usage["input_tokens"] - cached)
            bucket = cold if cached == 0 else warm
            bucket["requests"] = int(usage.get("completed_turns") or 1)
            bucket["uncached_prefix_kv_tokens"] = uncached
            bucket["cached_prefix_kv_tokens"] = cached

    return {
        "schema_version": "loopx_context_policy_telemetry_v0",
        "operator_counts": operator_counts,
        "quiet_turn_skips": quiet_turn_skips,
        "prefetch_latency_ms": prefetch_latency_ms,
        "prefetch_bytes": prefetch_bytes,
        "prefetch_bytes_saved": prefetch_bytes_saved,
        "round_trips_saved": round_trips_saved,
        "prefix_kv": {"cold": cold, "warm": warm},
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
