"""Shared telemetry, rollout metric extraction, and Base/Head comparison."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import contextvars
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import re
import time

from benchmark.runtime.session_observation import (
    CodexSessionObservationError,
    observe_codex_session_tail,
)
from loopx.control_plane.quota.codex_session_usage import (
    CodexSessionUsageError,
    read_codex_session_usage,
)
from loopx.control_plane.runtime.public_safety import (
    LOCAL_PATH_SURFACE_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
)
from loopx.control_plane.turn_driver.epoch_prefetch import (
    DEFAULT_TOKENIZER_ENCODING,
    count_verifiable_tokens,
)

MICROBENCH_RECEIPT_SCHEMA_VERSION = "loopx_microbench_receipt_v0"
MICROBENCH_DIFF_SCHEMA_VERSION = "loopx_microbench_differential_v0"

_EXTRA_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{12,})"
)
_USER_HOME_RE = re.compile(r"(?:/Users/|/home/|C:\\Users\\)[^\s\"',;]+")
_CODEX_COUNTER_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
_TRUNCATION_EVENT_TYPES = frozenset(
    {"truncated", "context_truncated", "history_truncated", "prompt_truncated"}
)
_ACTIVE_DEADLINE_MONOTONIC: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "microbench_active_deadline_monotonic",
    default=None,
)


class MicrobenchDeadlineExpired(TimeoutError):
    """Raised when a bounded microbenchmark phase exceeds its monotonic deadline."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"tier1_wall_budget_exhausted:{stage}")


@contextmanager
def microbench_deadline_scope(deadline_monotonic: float | None) -> Iterator[None]:
    """Bind an active monotonic wall deadline across nested scenario/CLI helpers."""
    token = _ACTIVE_DEADLINE_MONOTONIC.set(deadline_monotonic)
    try:
        yield
    finally:
        _ACTIVE_DEADLINE_MONOTONIC.reset(token)


def resolve_deadline(deadline_monotonic: float | None) -> float | None:
    """Return the tighter of an explicit deadline and any active context deadline."""
    active = _ACTIVE_DEADLINE_MONOTONIC.get()
    if deadline_monotonic is None:
        return active
    if active is None:
        return deadline_monotonic
    return min(deadline_monotonic, active)


def require_remaining_budget(deadline_monotonic: float, *, stage: str) -> float:
    """Return positive seconds remaining before ``deadline_monotonic`` or fail closed."""
    remaining = float(deadline_monotonic) - time.monotonic()
    if remaining <= 0.0:
        raise MicrobenchDeadlineExpired(stage)
    return remaining


@dataclass(frozen=True)
class BehaviorConstraintResult:
    """Typed outcome of all required M1/M2/M3 behavior constraints for an arm."""

    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    violations: tuple[str, ...] = ()

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 1.0 if self.passed else 0.0
        ok = sum(1 for value in self.checks.values() if value)
        return round(ok / len(self.checks), 4)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "checks": dict(self.checks),
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class EfficiencyMetrics:
    """Explicit token, context-window occupancy, byte, turn, and latency metrics."""

    wire_bytes: int | None = None
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls: int | None = None
    round_trips: int | None = None
    wall_seconds: float | None = None
    initial_turn_context_tokens: int | None = None
    per_turn_context_tokens: tuple[int, ...] | None = None
    peak_context_tokens: int | None = None
    effective_context_window: int | None = None
    min_headroom_tokens: int | None = None
    peak_window_ratio: float | None = None
    compaction_events: int | None = None
    truncation_events: int | None = None
    occupancy_measurement_kind: str = "unknown"
    occupancy_window_source: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        per_turn = payload.get("per_turn_context_tokens")
        if isinstance(per_turn, tuple):
            payload["per_turn_context_tokens"] = list(per_turn)
        return payload


def measure_text_tokens(
    text: str,
    *,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> tuple[int | None, dict[str, object] | None]:
    """Delegate verifiable prompt token counting to LoopX's shared tokenizer owner."""
    counted = count_verifiable_tokens(text, encoding_name=encoding_name)
    if counted is None:
        return None, None
    tokens, metadata = counted
    return tokens, dict(metadata)


def _valid_counter_map(raw: object) -> dict[str, int] | None:
    if not isinstance(raw, Mapping):
        return None
    parsed: dict[str, int] = {}
    for key in _CODEX_COUNTER_KEYS:
        val = raw.get(key)
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            return None
        parsed[key] = val
    return parsed


def compute_occupancy_summary(
    *,
    per_turn_context_tokens: Sequence[int] | None,
    effective_context_window: int | None = None,
    occupancy_measurement_kind: str | None = None,
    occupancy_window_source: str | None = None,
    compaction_events: int | None = None,
    truncation_events: int | None = None,
) -> dict[str, object]:
    """Compute typed initial/peak context-window occupancy and headroom summary."""
    valid_window = (
        effective_context_window
        if isinstance(effective_context_window, int)
        and not isinstance(effective_context_window, bool)
        and effective_context_window > 0
        else None
    )
    resolved_window_source = occupancy_window_source if valid_window is not None else None
    per_turn_tuple: tuple[int, ...] | None = None
    if per_turn_context_tokens:
        cleaned: list[int] = []
        valid_seq = True
        for val in per_turn_context_tokens:
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                valid_seq = False
                break
            cleaned.append(val)
        if valid_seq and cleaned:
            per_turn_tuple = tuple(cleaned)

    if per_turn_tuple is None:
        return {
            "initial_turn_context_tokens": None,
            "per_turn_context_tokens": None,
            "peak_context_tokens": None,
            "effective_context_window": valid_window,
            "min_headroom_tokens": None,
            "peak_window_ratio": None,
            "compaction_events": compaction_events,
            "truncation_events": truncation_events,
            "occupancy_measurement_kind": "unknown",
            "occupancy_window_source": resolved_window_source,
        }

    initial_ctx = per_turn_tuple[0]
    peak_ctx = max(per_turn_tuple)
    min_headroom = (valid_window - peak_ctx) if valid_window is not None else None
    peak_ratio = round(peak_ctx / valid_window, 6) if valid_window is not None else None
    return {
        "initial_turn_context_tokens": initial_ctx,
        "per_turn_context_tokens": per_turn_tuple,
        "peak_context_tokens": peak_ctx,
        "effective_context_window": valid_window,
        "min_headroom_tokens": min_headroom,
        "peak_window_ratio": peak_ratio,
        "compaction_events": compaction_events,
        "truncation_events": truncation_events,
        "occupancy_measurement_kind": occupancy_measurement_kind or "per_request_input_proxy",
        "occupancy_window_source": resolved_window_source,
    }


def _build_codex_telemetry_coverage(
    *,
    total_rollouts: int,
    verified_request_rollouts: int,
    verified_requests: int | None,
    complete_record_count: int | None,
    round_trips_reason: str | None = None,
) -> dict[str, object]:
    if (
        total_rollouts > 0
        and verified_request_rollouts == total_rollouts
        and verified_requests is not None
        and verified_requests > 0
    ):
        round_trips_cov: dict[str, object] = {
            "status": "complete",
            "reported_rollouts": verified_request_rollouts,
            "total_rollouts": total_rollouts,
            "verified_requests": verified_requests,
        }
        partial_metrics: list[str] = []
        missing_metrics: list[str] = []
    elif 0 < verified_request_rollouts < total_rollouts:
        round_trips_cov = {
            "status": "partial",
            "reported_rollouts": verified_request_rollouts,
            "total_rollouts": total_rollouts,
            **({"reason": round_trips_reason} if round_trips_reason else {}),
        }
        partial_metrics = ["round_trips"]
        missing_metrics = []
    else:
        round_trips_cov = {
            "status": "missing",
            "reported_rollouts": 0,
            "total_rollouts": total_rollouts,
            **({"reason": round_trips_reason} if round_trips_reason else {}),
        }
        partial_metrics = []
        missing_metrics = ["round_trips"]

    diagnostics: dict[str, object] = {
        "complete_record_count": complete_record_count,
        "complete_record_count_scope": "bounded_tail",
        "rollout_count": total_rollouts,
    }
    if round_trips_reason:
        diagnostics["round_trips_reason"] = round_trips_reason

    return {
        "schema_version": "microbench_telemetry_coverage_v0",
        "total_rollouts": total_rollouts,
        "has_partial_coverage": bool(partial_metrics),
        "partial_metrics": partial_metrics,
        "missing_metrics": missing_metrics,
        "metrics": {"round_trips": round_trips_cov},
        "diagnostics": diagnostics,
    }


def _parse_codex_rollout_occupancy(rollout: Path) -> dict[str, object]:
    """Validate per-request ``last_token_usage`` against ``total_token_usage`` deltas."""
    per_turn_inputs: list[int] = []
    windows: set[int] = set()
    compaction_events = 0
    truncation_events = 0
    previous = {key: 0 for key in _CODEX_COUNTER_KEYS}
    samples = 0
    per_request_valid = True
    token_count_events = 0
    occupancy_error: str | None = None

    with rollout.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                per_request_valid = False
                occupancy_error = occupancy_error or "invalid_rollout_json"
                continue
            if not isinstance(item, Mapping):
                per_request_valid = False
                occupancy_error = occupancy_error or "non_object_rollout_record"
                continue
            kind = str(item.get("type") or "")
            raw_payload = item.get("payload")
            payload = raw_payload if isinstance(raw_payload, Mapping) else {}
            ptype = str(payload.get("type") or "")
            if kind == "compacted" or (kind == "event_msg" and ptype == "compacted"):
                compaction_events += 1
                continue
            if (
                kind in _TRUNCATION_EVENT_TYPES
                or (kind == "event_msg" and ptype in _TRUNCATION_EVENT_TYPES)
                or payload.get("truncated") is True
            ):
                truncation_events += 1
            if kind != "event_msg" or ptype != "token_count":
                continue
            token_count_events += 1
            raw_info = payload.get("info")
            info = raw_info if isinstance(raw_info, Mapping) else {}
            win = info.get("model_context_window")
            if isinstance(win, int) and not isinstance(win, bool) and win > 0:
                windows.add(win)
            total = _valid_counter_map(info.get("total_token_usage"))
            last = _valid_counter_map(info.get("last_token_usage"))
            if total is None or last is None:
                per_request_valid = False
                occupancy_error = occupancy_error or "missing_last_token_usage"
                continue
            delta = {key: total[key] - previous[key] for key in _CODEX_COUNTER_KEYS}
            if samples == 0 and any(total[key] != last[key] for key in _CODEX_COUNTER_KEYS):
                per_request_valid = False
                occupancy_error = occupancy_error or "missing_initial_usage_prefix"
                continue
            if any(delta[key] < 0 for key in _CODEX_COUNTER_KEYS):
                per_request_valid = False
                occupancy_error = occupancy_error or "counter_reset"
                continue
            if any(delta[key] != 0 for key in _CODEX_COUNTER_KEYS):
                if any(delta[key] != last[key] for key in _CODEX_COUNTER_KEYS):
                    per_request_valid = False
                    occupancy_error = occupancy_error or "counter_gap"
                    continue
                per_turn_inputs.append(last["input_tokens"])
                previous = total
                samples += 1

    verified_all = per_request_valid and token_count_events > 0 and bool(per_turn_inputs)
    if token_count_events == 0:
        occupancy_error = occupancy_error or "missing_token_count_events"
    elif per_request_valid and not per_turn_inputs:
        occupancy_error = occupancy_error or "no_nonempty_token_count_delta"

    return {
        "per_turn_inputs": per_turn_inputs if verified_all else [],
        "verified_request_count": len(per_turn_inputs) if verified_all else None,
        "windows": windows,
        "compaction_events": compaction_events,
        "truncation_events": truncation_events,
        "per_request_valid": verified_all,
        "occupancy_error": occupancy_error if not verified_all else None,
    }


def collect_codex_rollout_metrics(
    sessions_root: Path,
    *,
    harbor_trajectories: Iterable[object] | None = None,
) -> dict[str, object]:
    """Extract usage metrics using ``codex_session_usage``, ``session_observation``, and Harbor."""
    if harbor_trajectories is not None:
        try:
            from benchmark.runtime.harbor import BenchmarkCodex

            totals = BenchmarkCodex._totals(harbor_trajectories)
            p_tot = totals.get("prompt")
            c_tot = totals.get("completion")
            total_tok = (
                p_tot + c_tot
                if isinstance(p_tot, int) and not isinstance(p_tot, bool)
                and isinstance(c_tot, int) and not isinstance(c_tot, bool)
                else None
            )
            return {
                "source": "harbor_trajectory_totals",
                "prompt_tokens": p_tot,
                "completion_tokens": c_tot,
                "cached_tokens": totals.get("cached"),
                "total_tokens": total_tok,
                "round_trips": None,
                "rollout_count": None,
                "complete_record_count": None,
                "telemetry_coverage": _build_codex_telemetry_coverage(
                    total_rollouts=0,
                    verified_request_rollouts=0,
                    verified_requests=None,
                    complete_record_count=None,
                    round_trips_reason="harbor_trajectory_totals",
                ),
                "error": None,
                **compute_occupancy_summary(per_turn_context_tokens=None),
            }
        except ImportError:
            pass

    rollouts = sorted(sessions_root.rglob("*.jsonl")) if sessions_root.is_dir() else []
    if not rollouts:
        return {
            "source": "codex_session_usage",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cached_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "round_trips": None,
            "rollout_count": 0,
            "complete_record_count": 0,
            "telemetry_coverage": _build_codex_telemetry_coverage(
                total_rollouts=0,
                verified_request_rollouts=0,
                verified_requests=None,
                complete_record_count=0,
                round_trips_reason="missing_rollout",
            ),
            "error": "missing_rollout",
            **compute_occupancy_summary(per_turn_context_tokens=None),
        }

    prompt_total = 0
    completion_total = 0
    cached_total = 0
    reasoning_total: int | None = 0
    grand_total: int | None = 0
    complete_records = 0
    all_per_turn_inputs: list[int] = []
    all_windows: set[int] = set()
    total_compactions = 0
    total_truncations = 0
    occupancy_valid = True
    occupancy_error: str | None = None
    verified_request_rollouts = 0

    for rollout in rollouts:
        try:
            usage = read_codex_session_usage(rollout)
            tail = observe_codex_session_tail(rollout)
            occ = _parse_codex_rollout_occupancy(rollout)
        except (CodexSessionUsageError, CodexSessionObservationError, OSError, ValueError) as exc:
            return {
                "source": "codex_session_usage",
                "prompt_tokens": None,
                "completion_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "round_trips": None,
                "rollout_count": len(rollouts),
                "complete_record_count": None,
                "telemetry_coverage": _build_codex_telemetry_coverage(
                    total_rollouts=len(rollouts),
                    verified_request_rollouts=0,
                    verified_requests=None,
                    complete_record_count=None,
                    round_trips_reason=type(exc).__name__,
                ),
                "error": type(exc).__name__,
                **compute_occupancy_summary(per_turn_context_tokens=None),
            }
        prompt_val = usage.get("input_tokens")
        comp_val = usage.get("output_tokens")
        cache_val = usage.get("cache_tokens")
        if (
            not isinstance(prompt_val, int) or isinstance(prompt_val, bool) or prompt_val < 0
            or not isinstance(comp_val, int) or isinstance(comp_val, bool) or comp_val < 0
            or not isinstance(cache_val, int) or isinstance(cache_val, bool) or cache_val < 0
        ):
            return {
                "source": "codex_session_usage",
                "prompt_tokens": None,
                "completion_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "round_trips": None,
                "rollout_count": len(rollouts),
                "complete_record_count": None,
                "telemetry_coverage": _build_codex_telemetry_coverage(
                    total_rollouts=len(rollouts),
                    verified_request_rollouts=0,
                    verified_requests=None,
                    complete_record_count=None,
                    round_trips_reason="incomplete_usage_fields",
                ),
                "error": "incomplete_usage_fields",
                **compute_occupancy_summary(per_turn_context_tokens=None),
            }
        prompt_total += prompt_val
        completion_total += comp_val
        cached_total += cache_val
        raw_rec = tail.get("complete_record_count")
        if isinstance(raw_rec, int) and not isinstance(raw_rec, bool) and raw_rec > 0:
            complete_records += raw_rec
        raw_tail_usage = tail.get("usage_cumulative")
        tail_usage: Mapping[str, object] = raw_tail_usage if isinstance(raw_tail_usage, Mapping) else {}
        r_val = tail_usage.get("reasoning_output_tokens")
        if isinstance(r_val, int) and not isinstance(r_val, bool) and r_val >= 0 and reasoning_total is not None:
            reasoning_total += r_val
        else:
            reasoning_total = None
        t_val = tail_usage.get("total_tokens")
        if isinstance(t_val, int) and not isinstance(t_val, bool) and t_val >= 0 and grand_total is not None:
            grand_total += t_val
        else:
            grand_total = None
        raw_comp = occ.get("compaction_events")
        raw_trunc = occ.get("truncation_events")
        if isinstance(raw_comp, int):
            total_compactions += raw_comp
        if isinstance(raw_trunc, int):
            total_truncations += raw_trunc
        raw_wins = occ.get("windows")
        if isinstance(raw_wins, set):
            all_windows.update(w for w in raw_wins if isinstance(w, int))
        raw_inputs = occ.get("per_turn_inputs")
        if occ.get("per_request_valid") and isinstance(raw_inputs, list) and raw_inputs:
            verified_request_rollouts += 1
            all_per_turn_inputs.extend(int(x) for x in raw_inputs)
        else:
            occupancy_valid = False
            if occupancy_error is None:
                raw_err = occ.get("occupancy_error")
                occupancy_error = str(raw_err) if raw_err else "invalid_per_request_usage"

    single_window = next(iter(all_windows)) if len(all_windows) == 1 else None
    all_requests_verified = (
        occupancy_valid
        and verified_request_rollouts == len(rollouts)
        and bool(all_per_turn_inputs)
    )
    verified_round_trips = len(all_per_turn_inputs) if all_requests_verified else None
    occ_summary = compute_occupancy_summary(
        per_turn_context_tokens=all_per_turn_inputs if all_requests_verified else None,
        effective_context_window=single_window,
        occupancy_measurement_kind="per_request_input_proxy",
        occupancy_window_source="codex_model_context_window" if single_window is not None else None,
        compaction_events=total_compactions,
        truncation_events=total_truncations,
    )
    coverage = _build_codex_telemetry_coverage(
        total_rollouts=len(rollouts),
        verified_request_rollouts=verified_request_rollouts,
        verified_requests=verified_round_trips,
        complete_record_count=complete_records,
        round_trips_reason=None if all_requests_verified else (occupancy_error or "invalid_per_request_usage"),
    )
    return {
        "source": "codex_session_usage",
        "prompt_tokens": prompt_total,
        "completion_tokens": completion_total,
        "cached_tokens": cached_total,
        "reasoning_tokens": reasoning_total,
        "total_tokens": grand_total,
        "round_trips": verified_round_trips,
        "rollout_count": len(rollouts),
        "complete_record_count": complete_records,
        "telemetry_coverage": coverage,
        "error": None,
        **({"occupancy_error": occupancy_error} if occupancy_error and not occupancy_valid else {}),
        **occ_summary,
    }


def _incomplete_metrics_from_coverage(coverage: Mapping[str, object] | None) -> set[str]:
    if not isinstance(coverage, Mapping):
        return set()
    incomplete: set[str] = set()
    raw_metrics = coverage.get("metrics")
    metric_map = raw_metrics if isinstance(raw_metrics, Mapping) else coverage
    for metric, info in metric_map.items():
        if isinstance(info, Mapping) and info.get("status") in {"partial", "missing"}:
            incomplete.add(str(metric))
    return incomplete


def _numeric_delta(base_value: int | float | None, head_value: int | float | None) -> int | float | None:
    if base_value is None or head_value is None:
        return None
    if isinstance(base_value, bool) or isinstance(head_value, bool):
        return None
    if not (math.isfinite(float(base_value)) and math.isfinite(float(head_value))):
        return None
    diff = head_value - base_value
    if isinstance(base_value, int) and isinstance(head_value, int):
        return int(diff)
    return round(float(diff), 4)


def compare_base_vs_head_efficiency(
    base_eff: EfficiencyMetrics,
    head_eff: EfficiencyMetrics,
    *,
    base_behavior: BehaviorConstraintResult,
    head_behavior: BehaviorConstraintResult,
    base_telemetry_coverage: Mapping[str, object] | None = None,
    head_telemetry_coverage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Compare Base vs Head on Behavior Constraint x Efficiency without masking regressions."""
    excluded_partial = sorted(
        _incomplete_metrics_from_coverage(base_telemetry_coverage)
        | _incomplete_metrics_from_coverage(head_telemetry_coverage)
    )
    excluded_set = set(excluded_partial)
    if "prompt_tokens" in excluded_set:
        excluded_set.update(
            {
                "initial_turn_context_tokens",
                "peak_context_tokens",
                "min_headroom_tokens",
                "peak_window_ratio",
            }
        )
    deltas: dict[str, int | float | None] = {}
    pct_changes: dict[str, float | None] = {}
    for field_name in (
        "wire_bytes",
        "prompt_tokens",
        "cached_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "tool_calls",
        "round_trips",
        "wall_seconds",
        "initial_turn_context_tokens",
        "peak_context_tokens",
        "effective_context_window",
        "min_headroom_tokens",
        "peak_window_ratio",
        "compaction_events",
        "truncation_events",
    ):
        base_val = getattr(base_eff, field_name)
        head_val = getattr(head_eff, field_name)
        if field_name in excluded_set:
            deltas[field_name] = None
            pct_changes[field_name] = None
            continue
        delta = _numeric_delta(base_val, head_val)
        deltas[field_name] = delta
        if delta is not None and isinstance(base_val, (int, float)) and base_val > 0:
            pct_changes[field_name] = round((float(delta) / float(base_val)) * 100.0, 2)
        else:
            pct_changes[field_name] = None

    baseline_valid = bool(base_behavior.passed)
    behavior_preserved = bool(
        baseline_valid
        and head_behavior.passed
        and head_behavior.pass_rate >= base_behavior.pass_rate
    )
    return {
        "behavior_preserved": behavior_preserved,
        "baseline_valid": baseline_valid,
        "base_passed": base_behavior.passed,
        "head_passed": head_behavior.passed,
        "base_pass_rate": base_behavior.pass_rate,
        "head_pass_rate": head_behavior.pass_rate,
        "base_violations": list(base_behavior.violations),
        "head_violations": list(head_behavior.violations),
        "excluded_partial_metrics": excluded_partial,
        "deltas": deltas,
        "pct_changes": pct_changes,
    }


def build_scenario_source_attribution(
    *,
    base_source: str,
    head_source: str = "candidate_head",
    comparison_kind: str,
    production_hot_path_covered: bool,
    note: str | None = None,
) -> dict[str, object]:
    """Record explicit source checkout & settlement attribution per scenario."""
    cross_commit = base_source == "external_base_checkout" and head_source == "candidate_head"
    return {
        "schema_version": "microbench_source_attribution_v0",
        "base_source": base_source,
        "head_source": head_source,
        "comparison_kind": comparison_kind,
        "production_hot_path_covered": production_hot_path_covered,
        "excluded_from_cross_commit_claims": not cross_commit,
        "excluded_from_production_hot_path_claims": not production_hot_path_covered,
        **({"note": note} if note else {}),
    }


def summarize_claim_scope(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarize which scenarios support cross-commit or production hot-path conclusions."""
    cross_commit: list[str] = []
    same_commit_mechanism: list[str] = []
    prod_covered: list[str] = []
    excluded_cross: list[str] = []
    excluded_prod: list[str] = []
    seen_cross: set[str] = set()
    seen_same: set[str] = set()
    seen_prod: set[str] = set()
    seen_ex_cross: set[str] = set()
    seen_ex_prod: set[str] = set()

    for row in rows:
        sid = str(row.get("scenario_id") or "")
        attr = row.get("source_attribution")
        if not sid or not isinstance(attr, Mapping):
            continue
        if attr.get("excluded_from_cross_commit_claims") is True:
            if sid not in seen_same:
                same_commit_mechanism.append(sid)
                seen_same.add(sid)
            if sid not in seen_ex_cross:
                excluded_cross.append(sid)
                seen_ex_cross.add(sid)
        elif sid not in seen_cross:
            cross_commit.append(sid)
            seen_cross.add(sid)
        if attr.get("excluded_from_production_hot_path_claims") is True:
            if sid not in seen_ex_prod:
                excluded_prod.append(sid)
                seen_ex_prod.add(sid)
        elif sid not in seen_prod:
            prod_covered.append(sid)
            seen_prod.add(sid)

    return {
        "schema_version": "microbench_claim_scope_v0",
        "cross_commit_scenarios": cross_commit,
        "same_commit_mechanism_scenarios": same_commit_mechanism,
        "production_hot_path_scenarios": prod_covered,
        "excluded_from_cross_commit_claims": excluded_cross,
        "excluded_from_production_hot_path_claims": excluded_prod,
    }


def _scan_text_violations(text: str) -> list[str]:
    findings: list[str] = []
    if _USER_HOME_RE.search(text):
        findings.append("local_home_path")
    if LOCAL_PATH_SURFACE_PATTERN.search(text):
        findings.append("local_path_surface")
    if SECRET_LIKE_SURFACE_PATTERN.search(text) or _EXTRA_SECRET_RE.search(text):
        findings.append("secret_like_token")
    return findings


def assert_public_safe_payload(payload: object, *, label: str = "microbench_receipt") -> None:
    """Fail closed if any serialized payload leaks local paths or credentials."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    violations = sorted(set(_scan_text_violations(encoded)))
    if violations:
        raise ValueError(f"{label} failed public safety boundary: {', '.join(violations)}")


def summarize_artifact_boundary(paths: Sequence[str] | Iterable[str] = ()) -> dict[str, object]:
    """Emit explicit boundary attestations matching LoopX benchmark contracts."""
    return {
        "raw_task_prompts_persisted": False,
        "raw_provider_bodies_persisted": False,
        "credentials_persisted": False,
        "local_paths_persisted": False,
        "synthetic_fixtures_only": True,
        "recorded_paths": list(paths),
    }
